"""``thalamus dream`` — run one dreaming cycle over the brain, and the shared
cycle composition that ``serve`` ticks in the background.

A dreaming cycle runs the gated passes (LinkResolutionPass refreshes the gateway's
derived views from durable truth; BeliefAuditPass proposes retiring beliefs whose
code has vanished) and records each to the dream log. The standalone command runs
exactly one cycle against a freshly-built gateway and prints the report — useful
to inspect proposals and to validate the passes against the real (dogfood) brain;
the refresh it applies lives for the process. In ``serve`` the same scheduler runs
on a background thread so the long-running brain stays fresh (see DreamTicker).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.protocols import Store, SupersessionIndex
from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.dreaming import (
    BeliefAuditPass,
    CycleReport,
    DreamLog,
    JsonlDreamLog,
    LinkResolutionPass,
    PassContext,
    PassStatus,
    Scheduler,
)
from thalamus.gateway import Gateway


def build_dream_scheduler(gateway: Gateway, *, dream_log: DreamLog | None = None) -> Scheduler:
    """The v0 pass set: the actor (view refresh) then the proposer (belief audit).

    Order follows the dreaming.md DAG flattened: link-resolution/staleness is an
    actor that refreshes what recall serves; the belief audit is a downstream
    proposer that only records suggestions."""
    return Scheduler(
        [LinkResolutionPass(gateway.refresh), BeliefAuditPass()],
        log=dream_log,
    )


def make_dream_context_factory(
    *,
    store: Store,
    supersession: SupersessionIndex | None,
    scope: Scope,
    repo: Path,
) -> Callable[[], PassContext]:
    """A factory that stamps a fresh ``now`` on each cycle's read-only context."""

    def make() -> PassContext:
        return PassContext(
            scope=scope,
            now=datetime.now(UTC),
            store=store,
            supersession=supersession,
            repo_root=str(repo),
        )

    return make


def dream_log_path(repo: Path) -> Path:
    return repo / ".thalamus" / "logs" / "dream.jsonl"


@dataclass(frozen=True, slots=True)
class DreamConfig:
    repo: Path
    tenant: str
    repo_id: str
    dim: int
    encoder: str
    resolve_calls: bool


def add_dream_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="repo whose AST is Brain 2 (default: cwd)"
    )
    parser.add_argument("--tenant", default="local", help="tenant id")
    parser.add_argument("--repo-id", default=None, help="repo id (default: repo dir name)")
    parser.add_argument("--dim", type=int, default=128, help="embedding dimensionality")
    parser.add_argument(
        "--encoder", choices=("bge-small", "deterministic"), default="bge-small",
        help="embedding model (default: bge-small; deterministic is for smoke tests)",
    )
    parser.add_argument(
        "--resolve-calls", action=argparse.BooleanOptionalAction, default=False,
        help="resolve Brain-2 call edges with jedi (off by default — a dream cycle does not "
        "need the call graph, and skipping it keeps the cycle fast)",
    )


def dream_config(args: argparse.Namespace) -> DreamConfig:
    repo = Path(args.repo).resolve()
    return DreamConfig(
        repo=repo,
        tenant=str(args.tenant),
        repo_id=str(args.repo_id) if args.repo_id else repo.name,
        dim=int(args.dim),
        encoder=str(args.encoder),
        resolve_calls=bool(args.resolve_calls),
    )


def _print_report(report: CycleReport) -> None:
    print(f"thalamus dream — cycle at {report.started_at.isoformat()}", file=sys.stderr)
    marks = {PassStatus.OK: "ok", PassStatus.SKIPPED: "skip", PassStatus.FAILED: "FAIL"}
    for p in report.passes:
        detail = p.summary or p.error or ""
        print(f"  [{marks[p.status]}] {p.name} ({p.kind.value}): {detail}", file=sys.stderr)
        for proposal in p.details.get("proposals", []):
            print(
                f"      ⚠ proposed supersede {proposal['memory_id']}: {proposal['reason']}",
                file=sys.stderr,
            )


def run_dream(config: DreamConfig) -> None:
    """Build the brain from durable state, run one dreaming cycle, and report."""
    # Lazy import breaks the serve<->dream module cycle (serve imports the cycle builders above).
    from thalamus.cli.brain import close_store
    from thalamus.cli.serve import ServeConfig, build_serve_gateway

    serve_config = ServeConfig(
        repo=config.repo,
        tenant=config.tenant,
        repo_id=config.repo_id,
        dim=config.dim,
        encoder=config.encoder,
        k=5,
        k_hop=1,
        resolve_calls=config.resolve_calls,
        structural_min_relevance=0.6,
        max_structural_items=12,
        max_memory_chars=1000,
        neo4j_uri=os.environ.get("THALAMUS_NEO4J_URI"),
        neo4j_user=os.environ.get("THALAMUS_NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("THALAMUS_NEO4J_PASSWORD"),
        session=False,
    )
    gateway, store, _episodes, supersession = build_serve_gateway(serve_config)
    try:
        scheduler = build_dream_scheduler(
            gateway, dream_log=JsonlDreamLog(dream_log_path(config.repo))
        )
        context = make_dream_context_factory(
            store=store,
            supersession=supersession,
            scope=Scope(TenantId(config.tenant), RepoId(config.repo_id)),
            repo=config.repo,
        )
        _print_report(scheduler.run(context()))
    finally:
        close_store(store)
