"""``thalamus.cli verdict`` — run the "does the brain help?" number on the real logs.

The composition root for the measurement loop (``docs/deep-dives/outcome-learned-retrieval.md``
§13.12): load the three durable JSONL logs and report both halves of the verdict —

- **Tier-1** ``utility@k`` (of the memories surfaced, what fraction proved used) over the
  retrieval-event + usage logs; and
- the **proxy↔truth** monitor: per-session Tier-1 ``utility@k`` joined to per-session Tier-2
  success (``classify_outcome`` over the session-bounded trajectory), flagging reward-hacking
  if utility reads high but fails to separate good outcomes from bad (§13.12, the Polynoica trap).

Tier-2 is grouped by **session** (the only coherent recall↔outcome unit — a commit can't be
mapped to the single session that informed it); the report surfaces **coverage** at both levels
so a confident-looking number backed by few labelled sessions stays visible (§13.13). This is
``eval`` machinery wired to the real logs; ``eval`` itself stays free of ``experiential`` —
the Tier-2 producer (``classify_outcome`` + the segmenter) lives here in the composition root.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from thalamus.core.types import SessionId
from thalamus.eval import (
    ProxyTruthReport,
    UtilityReport,
    join_proxy_truth,
    proxy_truth,
    session_utility,
    utility_at_k,
)
from thalamus.experiential import SessionBoundedSegmenter, classify_outcome, is_success
from thalamus.instrumentation import (
    RetrievalEvent,
    TrajectoryEvent,
    UsageSignal,
    read_event_log,
    read_trajectory_log,
    read_usage_log,
)


@dataclass(frozen=True, slots=True)
class VerdictConfig:
    repo: Path
    k: int
    retrieval_log: Path
    usage_log: Path
    trajectory_log: Path


@dataclass(frozen=True, slots=True)
class VerdictReport:
    k: int
    utility: UtilityReport  # Tier-1, over all events
    n_tier1_sessions: int  # sessions with a per-session Tier-1 utility
    n_tier2_sessions: int  # sessions with a captured Tier-2 outcome label
    monitor: ProxyTruthReport  # the joined proxy↔truth verdict
    monitor_coverage: float  # joined sessions / sessions with Tier-1


def add_verdict_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(),
        help="repo whose .thalamus/logs are read (default: cwd)",
    )
    parser.add_argument("--k", type=int, default=5, help="top-k cutoff for utility@k")
    parser.add_argument("--retrieval-log", type=Path, default=None, help="override path")
    parser.add_argument("--usage-log", type=Path, default=None, help="override path")
    parser.add_argument("--trajectory-log", type=Path, default=None, help="override path")


def verdict_config(args: argparse.Namespace) -> VerdictConfig:
    repo = Path(args.repo).resolve()
    logs = repo / ".thalamus" / "logs"
    return VerdictConfig(
        repo=repo,
        k=int(args.k),
        retrieval_log=Path(args.retrieval_log) if args.retrieval_log else logs / "retrieval.jsonl",
        usage_log=Path(args.usage_log) if args.usage_log else logs / "usage.jsonl",
        trajectory_log=(
            Path(args.trajectory_log) if args.trajectory_log else logs / "trajectory.jsonl"
        ),
    )


def _load[T](path: Path, reader: Callable[[Path], Iterator[T]]) -> list[T]:
    """Read a JSONL log into a list, treating a missing file as no data."""
    return list(reader(path)) if path.exists() else []


def tier2_by_session(trajectory: Iterable[TrajectoryEvent]) -> dict[SessionId, bool]:
    """Per-session Tier-2 success: segment the trajectory by session, classify each
    session's outcome, and keep only sessions with a real True/False label (an unknown
    outcome — ``COMMITTED``/``OPEN`` — is missing data, excluded, never counted False)."""
    result: dict[SessionId, bool] = {}
    for span in SessionBoundedSegmenter().segment(list(trajectory)):
        session_id = span.events[0].session_id
        if session_id is None:  # the segmenter excludes unkeyed events, but stay defensive
            continue
        success = is_success(classify_outcome(span.events))
        if success is not None:
            result[session_id] = success
    return result


def compute_verdict(
    events: list[RetrievalEvent],
    signals: list[UsageSignal],
    trajectory: list[TrajectoryEvent],
    *,
    k: int,
) -> VerdictReport:
    """The pure verdict computation over already-loaded logs (no I/O — unit-testable)."""
    tier1 = session_utility(events, signals, k)
    tier2 = tier2_by_session(trajectory)
    units = join_proxy_truth(tier1, tier2)
    return VerdictReport(
        k=k,
        utility=utility_at_k(events, signals, k),
        n_tier1_sessions=len(tier1),
        n_tier2_sessions=len(tier2),
        monitor=proxy_truth(units),
        monitor_coverage=len(units) / len(tier1) if tier1 else 0.0,
    )


def _render(report: VerdictReport) -> str:
    u = report.utility
    m = report.monitor
    lines = [
        f"Thalamus verdict — k={report.k}",
        "",
        f"Tier-1 utility@{report.k}: {u.utility_at_k:.3f}  "
        f"(n_events={u.n_events}, n_used={u.n_used}/{u.n_shown}, coverage={u.coverage:.2f})",
        f"Sessions: {report.n_tier1_sessions} with Tier-1 recalls, "
        f"{report.n_tier2_sessions} with a Tier-2 outcome label",
    ]
    if m.n_units == 0:
        lines.append(
            "proxy↔truth: no sessions present on BOTH sides yet — the loop is wired, but "
            "no joined recall→outcome data has accrued (expected until sessions run under "
            "the new session-keying)."
        )
    else:
        lines += [
            f"proxy↔truth: n_units={m.n_units}, coverage={report.monitor_coverage:.2f}",
            f"  mean utility:        {m.mean_utility:.3f}",
            f"  utility | success:   {m.mean_utility_success:.3f}",
            f"  utility | failure:   {m.mean_utility_failure:.3f}",
            f"  alignment:           {m.alignment:+.3f}  (>0: proxy tracks truth)",
            f"  reward-hacking suspected: {m.reward_hacking_suspected}",
        ]
    return "\n".join(lines)


def run_verdict(config: VerdictConfig) -> VerdictReport:
    """Load the real logs and report the measurement-loop verdict."""
    events = _load(config.retrieval_log, read_event_log)
    signals = _load(config.usage_log, read_usage_log)
    trajectory = _load(config.trajectory_log, read_trajectory_log)
    report = compute_verdict(events, signals, trajectory, k=config.k)
    print(_render(report))
    return report
