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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from thalamus.cli.brain import build_store, build_two_hemisphere_gateway, close_store
from thalamus.cli.dream import (
    build_credibility_pass,
    build_dream_scheduler,
    dream_log_path,
    make_dream_context_factory,
)
from thalamus.cli.remember import RememberConfig, run_remember
from thalamus.core.exceptions import ThalamusError
from thalamus.core.protocols import Encoder, Store, SupersessionIndex
from thalamus.core.types import (
    EventId,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    MemoryRef,
    RepoId,
    Scope,
    SessionId,
    TenantId,
)
from thalamus.dreaming import DreamTicker, JsonlDreamLog
from thalamus.experiential import Neo4jSupersessionIndex
from thalamus.gateway import Gateway
from thalamus.gateway.http_security import build_security_middleware
from thalamus.gateway.server import RecentReader, RememberWriter, ShownResolver
from thalamus.instrumentation import (
    FileSessionContextStore,
    JsonlEventSink,
    JsonlUsageSink,
    SessionContext,
    default_session_path,
    mint_session_id,
    read_event_log,
)
from thalamus.retrieval import render_recent, select_recent
from thalamus.routing import BgeEncoder, DeterministicEncoder
from thalamus.store import Neo4jStore, connect
from thalamus.structural import (
    CrossLinkIndex,
    FileManifest,
    Neo4jCrossLinkIndex,
    Neo4jFileManifest,
    Neo4jStructuralGraph,
    Neo4jStructuralIndex,
    StructuralGraph,
    StructuralIndex,
)

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
    session: bool = True
    session_id: str | None = None
    rebuild: bool = False
    dream_tick: bool = True
    dream_tick_minutes: float = 30.0
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    http_token: str | None = None
    allowed_origins: tuple[str, ...] = ()
    # Brain-2 code corpus language: "python" (AST + jedi) or a SCIP language (e.g.
    # "typescript") consuming a prebuilt --scip-index. Defaulted so existing callers are unaffected.
    code_language: str = "python"
    scip_index: Path | None = None
    # Extra doc roots ingested as their own labeled corpora (e.g. a design-docs dir outside the
    # code root). Empty = the single default docs corpus over --repo.
    doc_roots: tuple[Path, ...] = ()
    # Read-only investigation serve: recall (+ recent) only — no remember/record_usage, no
    # retrieval/usage logging, no dreaming. For inspecting a brain (e.g. a second process on the
    # same Neo4j) without writing to it OR contaminating its measurement with the inspection.
    investigate: bool = False
    # Where the brain's .thalamus data (logs, session, checkpoints) lives. Defaults to --repo;
    # set it to keep brain data OUT of the code root — e.g. serve a TS project at --repo
    # <mcp-server> but write the brain's data under an outer dir, leaving the code repo pristine.
    data_dir: Path | None = None


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
        "--code-language", choices=("python", "typescript"), default="python",
        help="Brain-2 code corpus language. 'python' uses the AST + jedi ingestors; "
        "'typescript' (and other SCIP languages) consumes a prebuilt --scip-index.",
    )
    parser.add_argument(
        "--scip-index", type=Path, default=None,
        help="path to a prebuilt .scip index (required for --code-language other than python). "
        "Build it out-of-band, e.g. scripts/scip-index-typescript.sh. NB: the index is the "
        "structure source-of-truth — regenerate it when the code changes.",
    )
    parser.add_argument(
        "--doc-root", type=Path, action="append", default=None, dest="doc_roots", metavar="DIR",
        help="ingest Markdown from this directory as its own 'Related docs (<dir>)' corpus; "
        "repeatable. Use for doc dirs OUTSIDE the code root (e.g. a sibling design-docs dir). "
        "When omitted, docs are taken from --repo as a single 'docs' corpus.",
    )
    parser.add_argument(
        "--resolve-calls", action=argparse.BooleanOptionalAction, default=True,
        help="resolve Brain-2 call edges with jedi at startup (--no-resolve-calls skips the cost; "
        "a no-op for SCIP languages, which already carry precise calls)",
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
    parser.add_argument(
        "--session", action=argparse.BooleanOptionalAction, default=True,
        help=(
            "tag recalls with a per-process session id and publish a session-context file "
            "so out-of-band capture (tests/commits) can join to it (--no-session leaves "
            "events unkeyed = the pre-measurement-loop behavior)"
        ),
    )
    parser.add_argument(
        "--session-id", default=None,
        help="explicit session id to use (default: mint a fresh one per serve process)",
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="force a full Brain-2 re-derive (drop the persisted graph + manifest) instead of "
        "an incremental rebuild — the re-derive oracle / recovery path",
    )
    parser.add_argument(
        "--dream-tick", action=argparse.BooleanOptionalAction, default=True,
        help="run dreaming on a background thread to keep derived views (superseded frontier, "
        "footprint staleness) fresh in the long-running serve (--no-dream-tick disables it)",
    )
    parser.add_argument(
        "--dream-tick-minutes", type=float, default=30.0,
        help="periodic dream-cycle interval in minutes (a remember/supersede also triggers one "
        "immediately); default 30",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=None,
        help="directory under which the brain's .thalamus data (logs/session/checkpoints) lives "
        "(default: --repo). Set it to keep brain data out of the code root — e.g. serve "
        "--repo <code> but write data under an outer project dir.",
    )
    parser.add_argument(
        "--investigate", action="store_true",
        help="read-only investigation serve: expose recall (+ recent) only — no remember / "
        "record_usage, no retrieval/usage logging, no dreaming. For inspecting a brain live "
        "without writing to it or polluting its verdict with the inspection itself.",
    )
    parser.add_argument(
        "--transport", choices=("stdio", "http"), default="stdio",
        help="MCP transport: stdio (default, one client, used by Claude Code) or http "
        "(Streamable HTTP, many concurrent clients over the network)",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="bind address for --transport http (default 127.0.0.1; set a LAN address to expose "
        "the brain to other machines — see --transport http security)",
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="bind port for --transport http (default 8000)"
    )


def serve_config(args: argparse.Namespace) -> ServeConfig:
    repo = Path(args.repo).resolve()
    code_language = str(args.code_language)
    scip_index = Path(args.scip_index).resolve() if args.scip_index else None
    if code_language != "python" and scip_index is None:
        raise ThalamusError(f"--code-language {code_language} requires --scip-index <path>")
    return ServeConfig(
        repo=repo,
        tenant=str(args.tenant),
        repo_id=str(args.repo_id) if args.repo_id else repo.name,
        dim=int(args.dim),
        encoder=str(args.encoder),
        k=int(args.k),
        k_hop=int(args.k_hop),
        code_language=code_language,
        scip_index=scip_index,
        doc_roots=tuple(Path(d).resolve() for d in (args.doc_roots or ())),
        investigate=bool(args.investigate),
        data_dir=Path(args.data_dir).resolve() if args.data_dir else None,
        resolve_calls=bool(args.resolve_calls),
        structural_min_relevance=float(args.structural_min_relevance),
        max_structural_items=int(args.max_structural_items),
        max_memory_chars=int(args.max_memory_chars),
        neo4j_uri=os.environ.get("THALAMUS_NEO4J_URI"),
        neo4j_user=os.environ.get("THALAMUS_NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("THALAMUS_NEO4J_PASSWORD"),
        session=bool(args.session),
        session_id=str(args.session_id) if args.session_id else None,
        rebuild=bool(args.rebuild),
        dream_tick=bool(args.dream_tick),
        dream_tick_minutes=float(args.dream_tick_minutes),
        transport=str(args.transport),
        host=str(args.host),
        port=int(args.port),
        # Cert-free HTTP security (env, not flags — secrets don't belong in argv/process lists):
        # optional bearer token + extra allow-listed Origins (localhost is always allowed).
        http_token=os.environ.get("THALAMUS_HTTP_TOKEN") or None,
        allowed_origins=tuple(
            o.strip()
            for o in os.environ.get("THALAMUS_HTTP_ALLOWED_ORIGINS", "").split(",")
            if o.strip()
        ),
    )


def build_serve_gateway(
    config: ServeConfig, *, store: Store | None = None, encoder: Encoder | None = None
) -> tuple[Gateway, Store, list[MemoryRecord], SupersessionIndex | None]:
    """Assemble the two-hemisphere gateway from durable Brain 1 + the current repo.

    Returns the gateway, the (open) store, the episodes scanned for re-linking, and the
    durable supersession index (when Neo4j-backed; ``None`` for an injected/in-memory store
    — the gateway then uses its own ephemeral index). ``store`` may be injected (tests);
    otherwise it is built from the Neo4j config."""
    encoder = encoder or (
        BgeEncoder("BAAI/bge-small-en-v1.5")
        if config.encoder == "bge-small"
        else DeterministicEncoder(dim=config.dim)
    )
    scope = Scope(tenant_id=TenantId(config.tenant), repo_id=RepoId(config.repo_id))
    # Persist Brain 2 (so restarts rebuild incrementally) when Neo4j is configured: one shared
    # driver backs the store, the structural graph + per-corpus indexes, the cross-links, and the
    # file manifest. Injected store (tests) or no Neo4j -> in-memory (a full re-derive each start).
    graph: StructuralGraph | None = None
    links: CrossLinkIndex | None = None
    code_index: StructuralIndex | None = None
    doc_index: StructuralIndex | None = None
    doc_index_factory: Callable[[str], StructuralIndex] | None = None
    manifest: FileManifest | None = None
    supersession: SupersessionIndex | None = None
    if store is None and config.neo4j_uri is not None:
        driver = connect(config.neo4j_uri, config.neo4j_user, config.neo4j_password or "")
        store = Neo4jStore(
            dim=encoder.dim, driver=driver, hemisphere=Hemisphere.EXPERIENTIAL,
            encoder_id=config.encoder,
        )
        graph = Neo4jStructuralGraph(driver, scope)
        links = Neo4jCrossLinkIndex(driver, scope)
        code_index = Neo4jStructuralIndex(
            driver, scope, dim=encoder.dim, corpus="code", encoder_id=config.encoder
        )
        doc_index = Neo4jStructuralIndex(
            driver, scope, dim=encoder.dim, corpus="docs", encoder_id=config.encoder
        )
        # Per-corpus Neo4j index for each extra --doc-root (each its own corpus tag/vector space).
        def doc_index_factory(corpus: str) -> StructuralIndex:
            return Neo4jStructuralIndex(
                driver, scope, dim=encoder.dim, corpus=corpus, encoder_id=config.encoder
            )

        manifest = Neo4jFileManifest(driver, scope)
        supersession = Neo4jSupersessionIndex(driver, scope)
    elif store is None:
        store = build_store(
            dim=encoder.dim, neo4j_uri=None, neo4j_user=config.neo4j_user,
            neo4j_password=config.neo4j_password, encoder_id=config.encoder,
        )
    episodes = store.scan(scope)  # cold-load Brain 1 to re-resolve cross-hemisphere links
    logs = (config.data_dir or config.repo) / ".thalamus" / "logs"
    gateway = build_two_hemisphere_gateway(
        config.repo,
        store=store,
        encoder=encoder,
        scope=scope,
        episodes=episodes,
        graph=graph,
        links=links,
        code_index=code_index,
        doc_index=doc_index,
        doc_roots=config.doc_roots or None,
        doc_index_factory=doc_index_factory,
        manifest=manifest,
        supersession=supersession,
        rebuild=config.rebuild,
        k=config.k,
        k_hop=config.k_hop,
        code_language=config.code_language,
        scip_index=config.scip_index,
        resolve_calls=config.resolve_calls,
        structural_min_relevance=config.structural_min_relevance,
        max_structural_items=config.max_structural_items,
        max_memory_chars=config.max_memory_chars,
        # Investigate mode logs nothing — inspecting a brain must not write retrieval/usage events
        # into its own logs (that would contaminate the verdict it is being used to check).
        event_sink=None if config.investigate else JsonlEventSink(logs / "retrieval.jsonl"),
        usage_sink=None if config.investigate else JsonlUsageSink(logs / "usage.jsonl"),
    )
    return gateway, store, episodes, supersession


def build_remember_writer(
    config: ServeConfig,
    *,
    store: Store,
    encoder: Encoder,
    supersession: SupersessionIndex | None = None,
    on_write: Callable[[], None] | None = None,
) -> RememberWriter:
    """Return the live MCP writer for explicit durable retained memories.

    ``supersession`` (when provided) lets a ``remember`` call mark a prior belief replaced
    (§13.18 D1) — the edge is persisted so the next serve demotes the old belief below
    current truth. ``on_write`` (the dream-tick trigger) is signalled after each write so a
    live ``remember --supersedes`` refreshes the served views promptly, not only on the next
    periodic cycle."""
    def write(
        kind: str,
        text: str,
        why: str | None,
        files: Sequence[str],
        importance: float,
        memory_id: str | None,
        supersedes: str | None,
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
            supersedes=supersedes,
            neo4j_uri=config.neo4j_uri,
            neo4j_user=config.neo4j_user,
            neo4j_password=config.neo4j_password,
        )
        record = run_remember(
            request, store=store, encoder=encoder, supersession=supersession, announce=False
        )
        if on_write is not None:
            on_write()  # write-trigger: dream a refresh soon so the new belief takes effect
        return record

    return write


def build_shown_resolver(store: Store, scope: Scope, retrieval_log: Path) -> ShownResolver:
    """Reconstruct a recall's shown ``(memory_id, content)`` pairs from the durable log + store.

    The durable fallback for ``record_usage`` when the in-memory payload is gone (serve restart,
    or another worker served the recall): stream the retrieval-event log for the matching event,
    then fetch each shown memory's *current* content from the store. A memory deleted since the
    recall is skipped (attribute over the surviving shown subset). Returns ``None`` when the event
    isn't in the log at all (a genuinely unknown event id).

    NB: a log miss is the exception (the in-memory cache covers same-process recalls), so the
    linear streaming scan is acceptable; an indexed lookup can replace it behind this same seam."""

    def resolve(event_id: EventId) -> list[tuple[MemoryId, str]] | None:
        event = next((e for e in read_event_log(retrieval_log) if e.event_id == event_id), None)
        if event is None:
            return None
        pairs: list[tuple[MemoryId, str]] = []
        for item in event.shown:
            record = store.get(MemoryRef(scope=scope, memory_id=item.memory_id))
            if record is not None:
                pairs.append((item.memory_id, record.content))
        return pairs

    return resolve


def build_recent_reader(store: Store, scope: Scope) -> RecentReader:
    """The temporal-query backend for the ``recent`` tool: scan durable memory, return the
    newest first. A backend ``ORDER BY created_at DESC LIMIT`` can replace the scan+sort here
    at scale, behind this same seam."""

    def read(limit: int, kind: str | None) -> str:
        records = select_recent(
            store.scan(scope), limit=limit, kinds=(kind,) if kind else None
        )
        return render_recent(records)

    return read


def run_serve(config: ServeConfig) -> None:
    """Build the brain from durable state and serve ``recall`` over MCP (blocking)."""
    from thalamus.gateway import build_server  # lazy: the 'mcp' extra

    encoder: Encoder = (
        BgeEncoder("BAAI/bge-small-en-v1.5")
        if config.encoder == "bge-small"
        else DeterministicEncoder(dim=config.dim)
    )
    gateway, store, episodes, supersession = build_serve_gateway(config, encoder=encoder)
    scope = Scope(TenantId(config.tenant), RepoId(config.repo_id))
    # Brain data home (logs/session/dream) — may differ from the code root (--repo).
    data_dir = config.data_dir or config.repo

    # Mint + publish a session id so recalls are keyed and out-of-band capture
    # (pytest plugin / git sync) can join to the same session — the measurement loop.
    default_session_id: SessionId | None = None
    if config.session:
        default_session_id = (
            SessionId(config.session_id) if config.session_id else mint_session_id()
        )
        FileSessionContextStore(default_session_path(data_dir)).publish(
            SessionContext(session_id=default_session_id, started_at=datetime.now(UTC))
        )

    # Dreaming on a background thread keeps the gateway's derived views (superseded frontier +
    # footprint staleness) fresh as writes accumulate — the long-running-serve refresh the frozen
    # composition-time dicts otherwise never get. Off the FastMCP event loop by construction.
    # Only meaningful with a durable supersession index (in-memory shell has nothing to refresh).
    ticker: DreamTicker | None = None
    if config.dream_tick and supersession is not None and not config.investigate:
        ticker = DreamTicker(
            build_dream_scheduler(
                gateway,
                dream_log=JsonlDreamLog(dream_log_path(data_dir)),
                # The credibility pass runs in the automatic loop too — logs from the data dir,
                # git reverts from the code root (they differ for e.g. dollhouse).
                credibility=build_credibility_pass(
                    logs_dir=data_dir, code_repo=config.repo, supersession=supersession, scope=scope
                ),
            ),
            make_dream_context_factory(
                store=store, supersession=supersession, scope=scope, repo=config.repo
            ),
            interval_seconds=max(config.dream_tick_minutes, 0.0) * 60.0,
        )

    server = build_server(
        gateway,
        scope,
        name=f"thalamus:{config.repo_id}",
        # Investigate mode is read-only: no remember writer (and build_server suppresses
        # record_usage), so the inspection connection cannot mutate or measure the brain.
        remember_writer=None if config.investigate else build_remember_writer(
            config,
            store=store,
            encoder=encoder,
            supersession=supersession,
            on_write=ticker.trigger if ticker is not None else None,
        ),
        read_only=config.investigate,
        default_session_id=default_session_id,
        resolve_shown=build_shown_resolver(
            store, scope, data_dir / ".thalamus" / "logs" / "retrieval.jsonl"
        ),
        # HTTP serves many clients from one process: key each recall by its MCP connection
        # session so concurrent agents don't collapse into the single process session. stdio
        # (one client) keeps the process session, which the out-of-band Tier-2 join relies on.
        per_connection_sessions=(config.transport == "http"),
        recent_reader=build_recent_reader(store, scope),
    )
    try:
        session_note = (
            f"session {default_session_id}" if default_session_id else "session tagging off"
        )
        dream_note = (
            f"dream-tick every {config.dream_tick_minutes:g}m" if ticker else "dream-tick off"
        )
        where = (
            f"HTTP http://{config.host}:{config.port}/mcp"
            if config.transport == "http"
            else "MCP (stdio)"
        )
        print(
            f"thalamus: serving [{config.repo_id}] over {where} — {len(episodes)} episodes, "
            f"Brain 2 re-derived from {config.repo}, {session_note}, {dream_note}. Ctrl-C to stop.",
            file=sys.stderr,
        )
        if ticker is not None:
            ticker.start()
        if config.transport == "http":
            host_is_local = config.host in ("127.0.0.1", "localhost", "::1")
            if not host_is_local and config.http_token is None:
                print(
                    f"thalamus: WARNING — bound to {config.host} (off-localhost) with no "
                    "THALAMUS_HTTP_TOKEN: anyone on the network can read/write this brain. "
                    "Set THALAMUS_HTTP_TOKEN, or front it with a VPN (e.g. Tailscale).",
                    file=sys.stderr,
                )
            middleware = [
                build_security_middleware(
                    allowed_origins=frozenset(config.allowed_origins), token=config.http_token
                )
            ]
            server.run(
                transport="http", host=config.host, port=config.port, middleware=middleware
            )
        else:
            server.run()
    finally:
        if ticker is not None:
            ticker.stop()
        close_store(store)
