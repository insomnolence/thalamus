"""``thalamus health`` — a one-screen health view of a brain, from its durable logs.

Composes the "does it help?" verdict (Tier-1 utility@k + the proxy↔truth monitor) with an
activity summary (recalls, commits, reverts, test runs green/red). Reads only the JSONL logs
under ``<repo>/.thalamus/logs`` — no Neo4j, no encoder — so it can inspect ANY brain's data
dir (e.g. a separate dollhouse brain) from anywhere, without serving it or generating events.

Designed for the periodic "is Thalamus doing the right thing?" check. It deliberately surfaces
whether NEGATIVE outcomes (reverts / red runs) are being captured at all — without them the
proxy↔truth alignment can't discriminate good work from bad, so the verdict is not yet meaningful.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from thalamus.cli.verdict import compute_verdict
from thalamus.instrumentation import (
    TrajectoryEvent,
    TrajectoryEventKind,
    read_event_log,
    read_trajectory_log,
    read_usage_log,
)


@dataclass(frozen=True, slots=True)
class HealthConfig:
    repo: Path
    k: int


def add_health_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(),
        help="brain repo whose .thalamus/logs to inspect (point at any brain's data dir)",
    )
    parser.add_argument("--k", type=int, default=5, help="top-k cutoff for utility@k")


def health_config(args: argparse.Namespace) -> HealthConfig:
    return HealthConfig(repo=Path(args.repo).resolve(), k=int(args.k))


def _load[T](path: Path, reader: Callable[[Path], Iterator[T]]) -> list[T]:
    return list(reader(path)) if path.exists() else []


def _failed(event: TrajectoryEvent) -> bool:
    return int(event.payload.get("failures", 0)) + int(event.payload.get("errors", 0)) > 0


def run_health(config: HealthConfig) -> None:
    logs = config.repo / ".thalamus" / "logs"
    events = _load(logs / "retrieval.jsonl", read_event_log)
    signals = _load(logs / "usage.jsonl", read_usage_log) + _load(
        logs / "usage_attributed.jsonl", read_usage_log
    )
    trajectory = _load(logs / "trajectory.jsonl", read_trajectory_log)
    verdict = compute_verdict(events, signals, trajectory, k=config.k)

    commits = sum(1 for e in trajectory if e.kind is TrajectoryEventKind.COMMIT)
    reverts = sum(1 for e in trajectory if e.kind is TrajectoryEventKind.REVERT)
    test_runs = [e for e in trajectory if e.kind is TrajectoryEventKind.TEST_RUN]
    red = sum(1 for e in test_runs if _failed(e))
    terminal = sum(1 for e in test_runs if e.payload.get("terminal"))

    u, m = verdict.utility, verdict.monitor
    lines = [
        f"Thalamus health — {config.repo.name}",
        f"  {config.repo}",
        "",
        "Does it help (verdict):",
        f"  Tier-1 utility@{config.k}: {u.utility_at_k:.3f}  "
        f"(n_events={u.n_events}, used={u.n_used}/{u.n_shown}, coverage={u.coverage:.2f})",
    ]
    if m.n_units == 0:
        lines.append(
            f"  proxy↔truth: not yet — {verdict.n_tier1_sessions} sessions with recalls, "
            f"{verdict.n_tier2_sessions} with an outcome label, 0 joined."
        )
    else:
        lines += [
            f"  proxy↔truth: n_units={m.n_units}, alignment={m.alignment:+.3f} "
            f"(>0: proxy tracks truth), reward-hacking={m.reward_hacking_suspected}",
        ]
    lines += [
        "",
        "Activity (trajectory log):",
        f"  recalls={len(events)}  usage-signals={len(signals)}  "
        f"commits={commits}  reverts={reverts}",
        f"  test runs={len(test_runs)} (green={len(test_runs) - red}, red={red}, "
        f"terminal={terminal})",
    ]
    if reverts == 0 and red == 0:
        lines.append(
            "  ⚠ no negative outcomes captured (no reverts, no red runs) — the verdict can't yet "
            "discriminate good work from bad; capture failures before trusting alignment."
        )
    print("\n".join(lines))
