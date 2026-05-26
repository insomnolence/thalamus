"""``thalamus.cli serve`` — a persistent MCP gateway over the two-hemisphere brain.

Cold-start serving (``docs/deep-dives/path-to-real-data.md``): connect to the durable
Brain 1 (Neo4j episodes), **scan** them (``Store.scan``) to re-resolve cross-hemisphere
links, **re-derive** Brain 2 by parsing the repo, assemble a :class:`Gateway`, and expose
its ``recall`` over MCP (FastMCP). Brain 1 is the durable source of truth; Brain 2 + links
are derived views rebuilt at startup (§14.1), so the served brain reflects the current code.

Needs the ``mcp`` extra (fastmcp) to actually run; the wiring (``build_serve_gateway``) is
import-light and unit-tested without it. Set ``THALAMUS_NEO4J_URI`` for a populated brain;
without it the store is in-memory (empty) and serving is a no-op shell (warned).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from thalamus.cli.brain import build_store, build_two_hemisphere_gateway, close_store
from thalamus.cli.remember import RememberConfig, run_remember
from thalamus.core.protocols import Encoder, Store
from thalamus.core.types import MemoryRecord, RepoId, Scope, TenantId
from thalamus.gateway import Gateway
from thalamus.gateway.server import RememberWriter
from thalamus.instrumentation import JsonlEventSink, JsonlUsageSink
from thalamus.routing import BgeEncoder, DeterministicEncoder

_DEFAULT_DIM = 128
_DEFAULT_ENCODER = "bge-small"


@dataclass(frozen=True, slots=True)
class ServeConfig:
    repo: Path
    tenant: str
    repo_id: str
    dim: int
    encoder: str
    k: int
    k_hop: int
    resolve_calls: bool
    structural_min_relevance: float
    max_structural_items: int
    max_memory_chars: int
    neo4j_uri: str | None
    neo4j_user: str
    neo4j_password: str | None


def add_serve_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="repo whose AST is Brain 2 (default: cwd)"
    )
    parser.add_argument("--tenant", default="local", help="tenant id to serve")
    parser.add_argument(
        "--repo-id", default=None, help="repo id to serve (default: repo dir name)"
    )
    parser.add_argument("--dim", type=int, default=_DEFAULT_DIM, help="embedding dimensionality")
    parser.add_argument(
        "--encoder", choices=("bge-small", "deterministic"), default=_DEFAULT_ENCODER,
        help="embedding model (default: bge-small; deterministic is for smoke tests)",
    )
    parser.add_argument("--k", type=int, default=5, help="memories per recall")
    parser.add_argument("--k-hop", type=int, default=1, help="structural hops to expand")
    parser.add_argument(
        "--resolve-calls", action=argparse.BooleanOptionalAction, default=True,
        help="resolve Brain-2 call edges with jedi at startup (--no-resolve-calls skips the cost)",
    )
    parser.add_argument(
        "--structural-min-relevance", type=float, default=0.6,
        help=(
            "minimum cosine for a direct structural hit. Default 0.6 is a conservative floor "
            "for BGE (observed: on-topic code >=0.70, off-topic noise ~0.57); lower it for other "
            "encoders. The encoder-agnostic Gateway default stays 0.0 — this is the BGE policy."
        ),
    )
    parser.add_argument(
        "--max-structural-items", type=int, default=12,
        help="maximum related code items returned in one context payload",
    )
    parser.add_argument(
        "--max-memory-chars", type=int, default=1000,
        help="maximum text returned for each recalled memory",
    )


def serve_config(args: argparse.Namespace) -> ServeConfig:
    repo = Path(args.repo).resolve()
    return ServeConfig(
        repo=repo,
        tenant=str(args.tenant),
        repo_id=str(args.repo_id) if args.repo_id else repo.name,
        dim=int(args.dim),
        encoder=str(args.encoder),
        k=int(args.k),
        k_hop=int(args.k_hop),
        resolve_calls=bool(args.resolve_calls),
        structural_min_relevance=float(args.structural_min_relevance),
        max_structural_items=int(args.max_structural_items),
        max_memory_chars=int(args.max_memory_chars),
        neo4j_uri=os.environ.get("THALAMUS_NEO4J_URI"),
        neo4j_user=os.environ.get("THALAMUS_NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("THALAMUS_NEO4J_PASSWORD"),
    )


def build_serve_gateway(
    config: ServeConfig, *, store: Store | None = None, encoder: Encoder | None = None
) -> tuple[Gateway, Store, list[MemoryRecord]]:
    """Assemble the two-hemisphere gateway from durable Brain 1 + the current repo.

    Returns the gateway, the (open) store, and the episodes scanned for re-linking.
    ``store`` may be injected (tests); otherwise it is built from the Neo4j config."""
    encoder = encoder or (
        BgeEncoder("BAAI/bge-small-en-v1.5")
        if config.encoder == "bge-small"
        else DeterministicEncoder(dim=config.dim)
    )
    if store is None:
        store = build_store(
            dim=encoder.dim,
            neo4j_uri=config.neo4j_uri,
            neo4j_user=config.neo4j_user,
            neo4j_password=config.neo4j_password,
            encoder_id=config.encoder,
        )
    scope = Scope(tenant_id=TenantId(config.tenant), repo_id=RepoId(config.repo_id))
    episodes = store.scan(scope)  # cold-load Brain 1 to re-resolve cross-hemisphere links
    logs = config.repo / ".thalamus" / "logs"
    gateway = build_two_hemisphere_gateway(
        config.repo,
        store=store,
        encoder=encoder,
        scope=scope,
        episodes=episodes,
        k=config.k,
        k_hop=config.k_hop,
        resolve_calls=config.resolve_calls,
        structural_min_relevance=config.structural_min_relevance,
        max_structural_items=config.max_structural_items,
        max_memory_chars=config.max_memory_chars,
        event_sink=JsonlEventSink(logs / "retrieval.jsonl"),
        usage_sink=JsonlUsageSink(logs / "usage.jsonl"),
    )
    return gateway, store, episodes


def build_remember_writer(
    config: ServeConfig, *, store: Store, encoder: Encoder
) -> RememberWriter:
    """Return the live MCP writer for explicit durable retained memories."""
    def write(
        kind: str,
        text: str,
        why: str | None,
        files: Sequence[str],
        importance: float,
        memory_id: str | None,
    ) -> MemoryRecord:
        request = RememberConfig(
            repo=config.repo,
            tenant=config.tenant,
            repo_id=config.repo_id,
            dim=config.dim,
            encoder=config.encoder,
            kind=kind,
            text=text,
            why=why,
            files=tuple(Path(item) for item in files),
            importance=importance,
            memory_id=memory_id,
            neo4j_uri=config.neo4j_uri,
            neo4j_user=config.neo4j_user,
            neo4j_password=config.neo4j_password,
        )
        return run_remember(request, store=store, encoder=encoder, announce=False)

    return write


def run_serve(config: ServeConfig) -> None:
    """Build the brain from durable state and serve ``recall`` over MCP (blocking)."""
    from thalamus.gateway import build_server  # lazy: the 'mcp' extra

    encoder: Encoder = (
        BgeEncoder("BAAI/bge-small-en-v1.5")
        if config.encoder == "bge-small"
        else DeterministicEncoder(dim=config.dim)
    )
    gateway, store, episodes = build_serve_gateway(config, encoder=encoder)
    try:
        print(
            f"thalamus: serving [{config.repo_id}] over MCP (stdio) — {len(episodes)} episodes, "
            f"Brain 2 re-derived from {config.repo}. Ctrl-C to stop.",
            file=sys.stderr,
        )
        scope = Scope(TenantId(config.tenant), RepoId(config.repo_id))
        build_server(
            gateway,
            scope,
            name=f"thalamus:{config.repo_id}",
            remember_writer=build_remember_writer(config, store=store, encoder=encoder),
        ).run()
    finally:
        close_store(store)
