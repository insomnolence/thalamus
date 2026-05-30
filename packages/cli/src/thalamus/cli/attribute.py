"""``thalamus.cli attribute`` — the deterministic Tier-1 usage signal, computed offline.

The composition root for footprint usage attribution (OLR §13.10 / §13.19): join each
session's recalls (what memories were surfaced) to that session's *actual work* (the commit
footprint), and mark a memory ``used`` when the code it is about overlaps the code the work
touched — via :class:`~thalamus.structural.FootprintAttributor` over the re-derived code
graph. This produces the **primary, deterministic** Tier-1 signal that the lexical citation
signal (`record_usage`) is too crude to give.

Reads the raw retrieval-event + trajectory logs and resolves each surfaced ``memory_id`` back
to its footprint via ``Store.scan`` (the log carries only ids). Writes a **derived** signal log
(``usage_attributed.jsonl``), overwritten each run (it is a re-derivable view of the raw logs +
the current code, §14.1 — never appended, so re-running is idempotent). ``verdict`` reads it
alongside the append-only citation log. A session with recalls but no committed work is skipped
(missing data, not a zero signal — §13.13).
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from thalamus.cli.brain import build_code_graph, build_store, close_store
from thalamus.core.protocols import Encoder, Store
from thalamus.core.types import EventId, MemoryId, RepoId, Scope, SessionId, TenantId
from thalamus.instrumentation import (
    JsonlUsageSink,
    RetrievalEvent,
    TrajectoryEvent,
    TrajectoryEventKind,
    UsageSignal,
    read_event_log,
    read_trajectory_log,
)
from thalamus.routing import BgeEncoder, DeterministicEncoder
from thalamus.structural import (
    FootprintAttributor,
    ShownMemory,
    StructuralGraph,
    StructuralNode,
    UsageAttributor,
)

_DEFAULT_DIM = 128
_DEFAULT_ENCODER = "bge-small"
# Grace past a recall-session's last recall in which a commit still counts as that session's
# work — covers "recall, then commit the change a bit later". Tunable via --window-hours.
_DEFAULT_WINDOW = timedelta(hours=2)


@dataclass(frozen=True, slots=True)
class AttributeConfig:
    repo: Path
    tenant: str
    repo_id: str
    dim: int
    encoder: str
    k_hop: int
    resolve_calls: bool
    retrieval_log: Path
    trajectory_log: Path
    attributed_log: Path
    neo4j_uri: str | None
    neo4j_user: str
    neo4j_password: str | None
    # Code corpus language for the footprint graph (TS/etc. need a prebuilt --scip-index), and
    # the data dir whose .thalamus/logs to read — decoupled from --repo (the code root), so a TS
    # project can be attributed with --repo <mcp-server> but logs read from an outer data dir.
    code_language: str = "python"
    scip_index: Path | None = None
    data_dir: Path | None = None
    window_hours: float = 2.0  # grace past a recall-session for counting a commit as its work


def add_attribute_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="repo whose code graph + logs to use"
    )
    parser.add_argument("--tenant", default="local", help="tenant id")
    parser.add_argument("--repo-id", default=None, help="repo id (default: repo dir name)")
    parser.add_argument("--dim", type=int, default=_DEFAULT_DIM, help="embedding dim (store)")
    parser.add_argument(
        "--encoder", choices=("bge-small", "deterministic"), default=_DEFAULT_ENCODER,
        help="encoder (only sizes the store for scanning; no embedding happens here)",
    )
    parser.add_argument(
        "--k-hop", type=int, default=1, help="structural hops for footprint overlap"
    )
    parser.add_argument(
        "--resolve-calls", action=argparse.BooleanOptionalAction, default=True,
        help="resolve jedi call edges for richer k-hop (--no-resolve-calls skips the ~9s cost)",
    )
    parser.add_argument(
        "--code-language", choices=("python", "typescript"), default="python",
        help="code corpus language for the footprint graph (must match the served brain)",
    )
    parser.add_argument(
        "--scip-index", type=Path, default=None,
        help="prebuilt .scip index (required for --code-language other than python)",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=None,
        help="directory whose .thalamus/logs to read (default: --repo)",
    )
    parser.add_argument(
        "--window-hours", type=float, default=2.0,
        help="grace (hours) past a recall-session's last recall in which a commit still counts "
        "as that session's work (the time-join that lets attribution work across agents)",
    )


def attribute_config(args: argparse.Namespace) -> AttributeConfig:
    repo = Path(args.repo).resolve()
    data_dir = Path(args.data_dir).resolve() if args.data_dir else repo
    logs = data_dir / ".thalamus" / "logs"
    return AttributeConfig(
        repo=repo,
        tenant=str(args.tenant),
        repo_id=str(args.repo_id) if args.repo_id else repo.name,
        dim=int(args.dim),
        encoder=str(args.encoder),
        k_hop=int(args.k_hop),
        resolve_calls=bool(args.resolve_calls),
        retrieval_log=logs / "retrieval.jsonl",
        trajectory_log=logs / "trajectory.jsonl",
        attributed_log=logs / "usage_attributed.jsonl",
        neo4j_uri=os.environ.get("THALAMUS_NEO4J_URI"),
        neo4j_user=os.environ.get("THALAMUS_NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("THALAMUS_NEO4J_PASSWORD"),
        code_language=str(args.code_language),
        scip_index=Path(args.scip_index).resolve() if args.scip_index else None,
        data_dir=data_dir,
        window_hours=float(args.window_hours),
    )


def compute_attribution(
    events: Iterable[RetrievalEvent],
    trajectory: Iterable[TrajectoryEvent],
    footprints: Mapping[MemoryId, Sequence[str]],
    attributor: UsageAttributor,
    *,
    window: timedelta = _DEFAULT_WINDOW,
) -> list[UsageSignal]:
    """Pure: attribute each recall-session's surfaced memories to the work committed around it.

    Recalls are grouped by their (agent) session and that session's recall time-span is tracked.
    The committed work is then joined by **time**, not by commit session id: a commit within the
    session's recall window (extended by ``window`` to catch the change a recall informed) counts
    as that session's work — regardless of which session, if any, the out-of-band commit was
    stamped with. This is what makes attribution work for a brain shared by multiple agents over
    HTTP, where recalls carry per-agent session ids but commits are stamped with the serve's
    session (or none), so an exact-session join finds nothing (§13.13). A recall-session whose
    window contains no commits is skipped — missing data, not zero usage."""
    recalls: dict[SessionId, list[tuple[EventId, list[ShownMemory]]]] = {}
    span: dict[SessionId, tuple[datetime, datetime]] = {}
    for event in events:
        if event.session_id is None:
            continue
        shown = [
            ShownMemory(item.memory_id, footprints.get(item.memory_id, ()))
            for item in event.shown
        ]
        recalls.setdefault(event.session_id, []).append((event.event_id, shown))
        lo, hi = span.get(event.session_id, (event.timestamp, event.timestamp))
        span[event.session_id] = (min(lo, event.timestamp), max(hi, event.timestamp))

    commits: list[tuple[datetime, list[str]]] = [
        (entry.timestamp, list(entry.payload.get("files", [])))
        for entry in trajectory
        if entry.kind is TrajectoryEventKind.COMMIT
    ]

    signals: list[UsageSignal] = []
    for session_id, session_recalls in recalls.items():
        lo, hi = span[session_id]
        session_work = {
            path for ts, files in commits if lo <= ts <= hi + window for path in files
        }
        if not session_work:
            continue  # no commits in the session's window — skip (missing data)
        for use in attributor.attribute(session_recalls, session_work):
            signals.append(
                UsageSignal(use.event_id, use.memory_id, use.connection, use.value, use.used)
            )
    return signals


def _build_encoder(config: AttributeConfig) -> Encoder:
    if config.encoder == "bge-small":
        return BgeEncoder("BAAI/bge-small-en-v1.5")
    return DeterministicEncoder(dim=config.dim)


def run_attribute(
    config: AttributeConfig,
    *,
    store: Store | None = None,
    encoder: Encoder | None = None,
    graph: StructuralGraph | None = None,
    nodes: Sequence[StructuralNode] | None = None,
) -> list[UsageSignal]:
    """Resolve footprints from the durable store, re-derive the code graph, attribute, and
    write the derived signal log (overwritten). Returns the signals. Concretes injectable."""
    encoder = encoder or _build_encoder(config)
    own_store = store is None
    if store is None:
        store = build_store(
            dim=encoder.dim, neo4j_uri=config.neo4j_uri,
            neo4j_user=config.neo4j_user, neo4j_password=config.neo4j_password,
            encoder_id=config.encoder,
        )
    try:
        scope = Scope(tenant_id=TenantId(config.tenant), repo_id=RepoId(config.repo_id))
        footprints: dict[MemoryId, Sequence[str]] = {
            record.memory_id: tuple(record.metadata.get("footprint", ()))
            for record in store.scan(scope)
        }
        if graph is None or nodes is None:
            graph, nodes = build_code_graph(
                config.repo, scope, resolve_calls=config.resolve_calls,
                code_language=config.code_language, scip_index=config.scip_index,
            )
        attributor = FootprintAttributor(graph, nodes, repo_root=config.repo, k_hop=config.k_hop)

        events = list(read_event_log(config.retrieval_log)) if config.retrieval_log.exists() else []
        trajectory = (
            list(read_trajectory_log(config.trajectory_log))
            if config.trajectory_log.exists()
            else []
        )
        signals = compute_attribution(
            events, trajectory, footprints, attributor,
            window=timedelta(hours=config.window_hours),
        )
    finally:
        if own_store:
            close_store(store)

    # The attributed log is a derived view: overwrite, never append, so re-runs are idempotent.
    config.attributed_log.parent.mkdir(parents=True, exist_ok=True)
    config.attributed_log.unlink(missing_ok=True)
    sink = JsonlUsageSink(config.attributed_log)
    for signal in signals:
        sink.emit(signal)
    used = sum(1 for signal in signals if signal.used)
    print(
        f"attributed {len(signals)} footprint usage signal(s) ({used} used) "
        f"-> {config.attributed_log}"
    )
    return signals
