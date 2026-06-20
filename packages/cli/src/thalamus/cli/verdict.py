"""``thalamus.cli verdict`` — run the "does the brain help?" number on the real logs.

The composition root for the measurement loop (``docs/deep-dives/outcome-learned-retrieval.md``
§13.12): load the three durable JSONL logs and report both halves of the verdict —

- **Tier-1** ``utility@k`` (of the memories surfaced, what fraction proved used) over the
  retrieval-event + usage logs; and
- the **proxy↔truth** monitor: per-session Tier-1 ``utility@k`` joined to per-session Tier-2
  success (``classify_outcome`` over the session-bounded trajectory), flagging reward-hacking
  if utility reads high but fails to separate good outcomes from bad (§13.12, the
  self-validation trap).

Tier-2 is grouped by **session** (the only coherent recall↔outcome unit — a commit can't be
mapped to the single session that informed it); the report surfaces **coverage** at both levels
so a confident-looking number backed by few labelled sessions stays visible (§13.13). This is
``eval`` machinery wired to the real logs; ``eval`` itself stays free of ``experiential`` —
the Tier-2 producer (``classify_outcome`` + the segmenter) lives here in the composition root.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from thalamus.core.types import SessionId
from thalamus.eval import (
    ProxyTruthReport,
    UsageStabilityReport,
    UtilityReport,
    join_proxy_truth,
    proxy_truth,
    session_utility,
    usage_stability,
    utility_at_k,
)
from thalamus.experiential import (
    FateSignals,
    FateVerdict,
    GitSurvivalLabeler,
    SessionBoundedSegmenter,
    assess_fate,
    classify_outcome,
    fate_success,
    is_success,
    region_fate,
)
from thalamus.instrumentation import (
    RetrievalEvent,
    TrajectoryEvent,
    TrajectoryEventKind,
    UsageSignal,
    read_event_log,
    read_trajectory_log,
    read_usage_log,
    reverted_shas,
)

_DEFAULT_WINDOW = timedelta(hours=2)


@dataclass(frozen=True, slots=True)
class VerdictConfig:
    repo: Path
    k: int
    retrieval_log: Path
    usage_log: Path  # raw citation signals (record_usage, append-only)
    attributed_log: Path  # derived footprint signals (thalamus attribute, overwritten)
    trajectory_log: Path


@dataclass(frozen=True, slots=True)
class VerdictReport:
    k: int
    utility: UtilityReport  # Tier-1, over all events
    n_tier1_sessions: int  # sessions with a per-session Tier-1 utility
    n_tier2_sessions: int  # sessions with a captured Tier-2 outcome label
    monitor: ProxyTruthReport  # proxy↔truth with Tier-2 = structural fate (session-work)
    monitor_coverage: float  # joined sessions / sessions with Tier-1
    n_negative_sessions: int  # sessions whose work fated negative (reverted or overwritten)
    monitor_without_fate: ProxyTruthReport  # classify (test path) alone — usually empty
    usage: UsageStabilityReport  # per-memory usefulness: is "used vs. ignored" stable, not noise?


def add_verdict_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(),
        help="repo whose .thalamus/logs are read (default: cwd)",
    )
    parser.add_argument("--k", type=int, default=5, help="top-k cutoff for utility@k")
    parser.add_argument("--retrieval-log", type=Path, default=None, help="override path")
    parser.add_argument("--usage-log", type=Path, default=None, help="override path")
    parser.add_argument("--attributed-log", type=Path, default=None, help="override path")
    parser.add_argument("--trajectory-log", type=Path, default=None, help="override path")


def verdict_config(args: argparse.Namespace) -> VerdictConfig:
    repo = Path(args.repo).resolve()
    logs = repo / ".thalamus" / "logs"
    return VerdictConfig(
        repo=repo,
        k=int(args.k),
        retrieval_log=Path(args.retrieval_log) if args.retrieval_log else logs / "retrieval.jsonl",
        usage_log=Path(args.usage_log) if args.usage_log else logs / "usage.jsonl",
        attributed_log=(
            Path(args.attributed_log) if args.attributed_log else logs / "usage_attributed.jsonl"
        ),
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


def _session_spans(
    events: Iterable[RetrievalEvent],
) -> dict[SessionId, tuple[datetime, datetime]]:
    """Each keyed session's recall time-span ``(first, last)`` — the window the out-of-band
    time-join (commits, test runs) is measured against. Unkeyed events are skipped."""
    spans: dict[SessionId, tuple[datetime, datetime]] = {}
    for event in events:
        if event.session_id is None:
            continue
        lo, hi = spans.get(event.session_id, (event.timestamp, event.timestamp))
        spans[event.session_id] = (min(lo, event.timestamp), max(hi, event.timestamp))
    return spans


def session_struggle(
    events: Iterable[RetrievalEvent],
    trajectory: Iterable[TrajectoryEvent],
    *,
    window: timedelta = _DEFAULT_WINDOW,
    fail_threshold: int = 2,
) -> frozenset[SessionId]:
    """⚠️ PARKED (2026-06-15, candidate for removal) — part of the *outcome* loop the learning track
    was re-aimed away from (ROADMAP Track L / retained:dc134e4a). Needs captured failing test runs,
    which this workflow doesn't produce. Kept + tested; remove if instrumented coding never resumes.

    Sessions that **struggled** — ``>= fail_threshold`` failing test runs in the session window.

    The dead-end / "banging on it" negative that never reaches a commit, so the revert/overwrite
    fate (:func:`session_fate`) misses it entirely — and which ``classify_outcome`` deliberately
    discards (an intermediate red "may have been fixed before the commit"). It is a **weak**
    negative: :func:`compute_verdict` applies it last, only to sessions the stronger signals leave
    UNKNOWN, so it never overrides a clean success — it surfaces negatives that live in the
    *process*, not the terminal state. Test runs are joined by time, like ``session_fate``."""
    spans = _session_spans(events)
    failed_runs = sorted(
        event.timestamp
        for event in trajectory
        if event.kind is TrajectoryEventKind.TEST_RUN
        and int(event.payload.get("failures", 0)) + int(event.payload.get("errors", 0)) > 0
    )
    struggling = {
        session_id
        for session_id, (lo, hi) in spans.items()
        if sum(1 for ts in failed_runs if lo <= ts <= hi + window) >= fail_threshold
    }
    return frozenset(struggling)


def session_fate(
    events: Iterable[RetrievalEvent],
    trajectory: Iterable[TrajectoryEvent],
    reverted: frozenset[str],
    *,
    window: timedelta = _DEFAULT_WINDOW,
    survival_threshold: int = 5,
    commit_lines: Mapping[str, tuple[int, int]] | None = None,
) -> dict[SessionId, FateVerdict]:
    """Per-session fate of the work the session committed (OLR §13.17, the session-work consumer).

    NEGATIVE if any of the session's commits were later reverted **or its own code was largely
    overwritten** (the fix-forward negative — the dead-end is a rewrite, not a `git revert`);
    POSITIVE if its work survived. Commits are joined to a session by **time** (its recall span +
    ``window``), the same out-of-band join Tier-1 footprint attribution uses (so it holds for a
    brain shared by many agents over HTTP). Sessions with no committed work in window are omitted.

    ``commit_lines`` (sha → ``(introduced, surviving)`` from
    :meth:`~thalamus.experiential.labeler.GitSurvivalLabeler.commit_line_stats`) is the survival
    signal: a session's own commits' lines are summed and run through :func:`region_fate`, so a
    session whose code was rewritten reads NEGATIVE even with no revert. Without it (back-compat)
    the crude "later commit count" stands in for survival — which, lacking reverts, can only read
    positive (the famine this parameter fixes).

    ⚠️ The ``commit_lines`` churn path is PARKED (2026-06-15, candidate for removal) — the *outcome*
    loop the learning track was re-aimed away from (ROADMAP Track L / retained:dc134e4a). The
    revert/`later` path stays as the back-compat default."""
    spans = _session_spans(events)
    commits = [
        (event.timestamp, str(event.payload.get("sha", "")))
        for event in trajectory
        if event.kind is TrajectoryEventKind.COMMIT
    ]
    result: dict[SessionId, FateVerdict] = {}
    for session_id, (lo, hi) in spans.items():
        cutoff = hi + window
        own = {sha for timestamp, sha in commits if sha and lo <= timestamp <= cutoff}
        if not own:
            continue
        later = sum(1 for timestamp, _ in commits if timestamp > cutoff)
        reverted_flag = bool(own & reverted)
        signals = FateSignals(reverted=reverted_flag, survived_activity=later)
        if commit_lines is not None:
            introduced = sum(commit_lines[sha][0] for sha in own if sha in commit_lines)
            surviving = sum(commit_lines[sha][1] for sha in own if sha in commit_lines)
            if introduced > 0:  # region-survival of the session's OWN work (the real negative)
                churn, survived = region_fate(
                    introduced=introduced, surviving=surviving, exercising_commits=later
                )
                signals = FateSignals(
                    reverted=reverted_flag, churn_ratio=churn, survived_activity=survived
                )
        result[session_id] = assess_fate(signals, survival_threshold=survival_threshold)
    return result


def compute_verdict(
    events: list[RetrievalEvent],
    signals: list[UsageSignal],
    trajectory: list[TrajectoryEvent],
    *,
    k: int,
    reverted: frozenset[str] = frozenset(),
    window: timedelta = _DEFAULT_WINDOW,
    commit_lines: Mapping[str, tuple[int, int]] | None = None,
) -> VerdictReport:
    """The pure verdict computation over already-loaded logs (no I/O — unit-testable).

    **Tier-2 is the structural fate** of each session's committed work — kept-vs-reverted via
    :func:`session_fate` (survived → success, reverted → failure) — the decided truth signal
    (dreaming.md "Pass: fate-based credibility"). The test-based ``classify_outcome`` is kept only
    as a **fallback** for sessions fate leaves unlabelled, and is usually absent in a workflow
    without captured test runs. :func:`session_struggle` adds the weakest, last-resort negative
    (repeated in-session test failures) for sessions still unlabelled — the dead-ends that never
    reach a commit. ``monitor_without_fate`` reports the test path alone for comparison, making it
    visible that the fate signal is what carries the verdict."""
    tier1 = session_utility(events, signals, k)
    fate = session_fate(events, trajectory, reverted, window=window, commit_lines=commit_lines)
    fate_tier2: dict[SessionId, bool] = {}
    for session, verdict in fate.items():
        success = fate_success(verdict)
        if success is not None:
            fate_tier2[session] = success
    classify_tier2 = tier2_by_session(trajectory)  # optional fallback (needs tests/terminal/revert)
    # Structural fate is the primary Tier-2; classify (test path) fills sessions fate left
    # unlabelled. A failure from either source wins — a revert or a real test-fail is a precious
    # negative we never let a weak survival-positive overwrite.
    tier2: dict[SessionId, bool] = dict(classify_tier2)
    for session, success in fate_tier2.items():
        if success is False or session not in tier2:
            tier2[session] = success
    # Weakest negative, applied LAST: in-session struggle fills only sessions still unlabelled, so
    # a rocky-but-successful session (terminal-green / survived) keeps its positive (§14.4).
    struggling = session_struggle(events, trajectory, window=window)
    for session in struggling:
        tier2.setdefault(session, False)
    units = join_proxy_truth(tier1, tier2)
    negative_sessions = {session for session, label in tier2.items() if label is False}
    return VerdictReport(
        k=k,
        utility=utility_at_k(events, signals, k),
        n_tier1_sessions=len(tier1),
        n_tier2_sessions=len(tier2),
        monitor=proxy_truth(units),
        monitor_coverage=len(units) / len(tier1) if tier1 else 0.0,
        n_negative_sessions=len(negative_sessions),
        monitor_without_fate=proxy_truth(join_proxy_truth(tier1, classify_tier2)),
        usage=usage_stability(events, signals),
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
    lines.append(
        f"Tier-2 = structural fate: {report.n_tier2_sessions} session(s) labelled, "
        f"{report.n_negative_sessions} fate-negative (reverted or overwritten) "
        f"(classify/test-path alone would join {report.monitor_without_fate.n_units})"
    )
    s = report.usage
    if s.n_eligible == 0:
        lines.append(
            f"Usage stability: no memory surfaced >={s.min_surfaced}x with a captured "
            "outcome yet — needs more usage volume."
        )
    else:
        lines += [
            f"Usage stability: {s.n_eligible} memories surfaced >={s.min_surfaced}x — "
            f"{s.n_reliable} reliably-used / {s.n_ignored} reliably-ignored / {s.n_mixed} mixed "
            f"(separation={s.separation:.2f}, mean rate={s.mean_rate:.2f})",
            f"  cross-session reuse: {s.n_reused} memory(ies) used in >=2 sessions "
            f"(max {s.max_reuse}) — the reliably-useful core",
        ]
    return "\n".join(lines)


def run_verdict(config: VerdictConfig) -> VerdictReport:
    """Load the real logs and report the measurement-loop verdict."""
    events = _load(config.retrieval_log, read_event_log)
    # Both Tier-1 producers feed the join: the deterministic footprint signal (primary,
    # derived by `attribute`) and the citation signal (secondary, from record_usage).
    signals = _load(config.usage_log, read_usage_log) + _load(config.attributed_log, read_usage_log)
    trajectory = _load(config.trajectory_log, read_trajectory_log)
    # Region-survival of each session's own commits — the fix-forward negative (a rewrite, not a
    # revert). Reads the committed files straight from the trajectory log and asks git how much of
    # each commit survived at HEAD. I/O-heavy (a blame per file), but verdict is an offline command.
    commit_files = [
        (str(event.payload.get("sha", "")), [str(f) for f in event.payload.get("files", ())])
        for event in trajectory
        if event.kind is TrajectoryEventKind.COMMIT and event.payload.get("sha")
    ]
    commit_lines = GitSurvivalLabeler(config.repo).commit_line_stats(commit_files)
    report = compute_verdict(
        events, signals, trajectory, k=config.k, reverted=reverted_shas(config.repo),
        commit_lines=commit_lines,
    )
    print(_render(report))
    return report
