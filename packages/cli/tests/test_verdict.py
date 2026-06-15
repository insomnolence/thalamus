from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from thalamus.cli.verdict import (
    add_verdict_arguments,
    compute_verdict,
    run_verdict,
    verdict_config,
)
from thalamus.core.types import EventId, MemoryId, RepoId, Scope, SessionId, TenantId
from thalamus.instrumentation import (
    JsonlEventSink,
    JsonlTrajectorySink,
    JsonlUsageSink,
    RetrievalEvent,
    ShownItem,
    TrajectoryEvent,
    TrajectoryEventKind,
    UsageSignal,
    build_test_run_event,
)

SCOPE = Scope(TenantId("local"), RepoId("r"))
NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)


def _event(eid: str, session: str, shown: list[str]) -> RetrievalEvent:
    return RetrievalEvent(
        event_id=EventId(eid), timestamp=NOW, scope=SCOPE, policy_id="L0", cue_text="q",
        k_requested=len(shown),
        candidates=[],
        shown=[ShownItem(MemoryId(m), rank=i, propensity=1.0) for i, m in enumerate(shown)],
        session_id=SessionId(session),
    )


def _signal(eid: str, mid: str, *, used: bool) -> UsageSignal:
    return UsageSignal(EventId(eid), MemoryId(mid), "overlap", 1.0 if used else 0.0, used)


def _commit(eid: str, sha: str) -> TrajectoryEvent:
    return TrajectoryEvent(
        event_id=EventId(eid),
        timestamp=NOW,
        scope=SCOPE,
        kind=TrajectoryEventKind.COMMIT,
        payload={"sha": sha, "files": ["a.py"]},
    )


def test_revert_forces_a_session_negative_that_classify_misses() -> None:
    # s1 recalls + uses m1 and commits work (no test run → classify leaves it COMMITTED/excluded);
    # that commit is later reverted → session-work fate forces s1 negative → a proxy<->truth unit
    # that the in-span classifier alone would never have produced.
    events = [_event("e1", "s1", ["m1"])]
    signals = [_signal("e1", "m1", used=True)]
    trajectory = [_commit("c1", "deadbeef")]

    report = compute_verdict(events, signals, trajectory, k=5, reverted=frozenset({"deadbeef"}))
    assert report.n_negative_sessions == 1
    assert report.monitor.n_units == 1  # the fate negative created a joinable unit
    assert report.monitor_without_fate.n_units == 0  # classify alone labelled nothing

    # Without a matching revert, the same session yields no Tier-2 label (COMMITTED → excluded).
    clean = compute_verdict(events, signals, trajectory, k=5, reverted=frozenset())
    assert clean.n_negative_sessions == 0
    assert clean.monitor.n_units == 0


def test_overwritten_session_work_is_a_negative_without_a_revert() -> None:
    # The fix-forward negative: s1's commit was never git-reverted, but its lines were later
    # overwritten (commit_lines: introduced=10, surviving=0 → churn 1.0) → session reads NEGATIVE.
    # The crude "later commit count" path could never produce this — it's the famine fix.
    events = [_event("e1", "s1", ["m1"])]
    signals = [_signal("e1", "m1", used=True)]
    trajectory = [_commit("c1", "abc123")]

    overwritten = compute_verdict(
        events, signals, trajectory, k=5, commit_lines={"abc123": (10, 0)}
    )
    assert overwritten.n_negative_sessions == 1  # churn (not revert) produced the negative
    assert overwritten.monitor.n_units == 1  # ...and a joinable Tier-2 unit the crude path can't

    # Same session, work survived intact (surviving == introduced) → churn 0 → no forced negative.
    survived = compute_verdict(
        events, signals, trajectory, k=5, commit_lines={"abc123": (10, 10)}
    )
    assert survived.monitor.n_units == 0  # no negative; nothing else labels it → excluded


def _test_run(eid: str, session: str, *, failures: int) -> object:
    return build_test_run_event(
        event_id=EventId(eid), timestamp=NOW, scope=SCOPE, tests=1, failures=failures,
        errors=0, skipped=0, failed=[], terminal=True, session_id=SessionId(session),
    )


def _failrun(eid: str, session: str) -> object:
    # a NON-terminal failing run — classify_outcome leaves the span UNKNOWN, so only
    # session_struggle can catch it (the in-process dead-end signal).
    return build_test_run_event(
        event_id=EventId(eid), timestamp=NOW, scope=SCOPE, tests=3, failures=1,
        errors=0, skipped=0, failed=["t"], terminal=False, session_id=SessionId(session),
    )


def test_in_session_struggle_is_a_last_resort_negative_for_dead_ends() -> None:
    # s1 recalls + uses m1, then two failing (non-terminal) runs, and never commits. Commit-fate
    # and classify both leave it UNKNOWN (no commit, no terminal) → it would be excluded — but the
    # struggle signal labels it NEGATIVE, the dead-end that never reached a commit.
    events = [_event("e1", "s1", ["m1"])]
    signals = [_signal("e1", "m1", used=True)]
    report = compute_verdict(events, signals, [_failrun("t1", "s1"), _failrun("t2", "s1")], k=5)
    assert report.n_negative_sessions == 1
    assert report.monitor.n_units == 1  # a joinable unit neither fate nor classify could produce

    # A single failure is below the struggle threshold → no label → still excluded (not over-eager).
    one = compute_verdict(events, signals, [_failrun("t1", "s1")], k=5)
    assert one.monitor.n_units == 0


def test_struggle_does_not_override_a_terminal_success() -> None:
    # s1 struggled (2 failing runs) but then a terminal-green run → classify PASSED. The weak
    # struggle negative only fills sessions the stronger signals leave unlabelled, so it must NOT
    # turn this rocky-but-successful session negative (§14.4 conservatism).
    events = [_event("e1", "s1", ["m1"])]
    signals = [_signal("e1", "m1", used=True)]
    trajectory = [_failrun("t1", "s1"), _failrun("t2", "s1"), _test_run("t3", "s1", failures=0)]
    report = compute_verdict(events, signals, trajectory, k=5)
    assert report.n_negative_sessions == 0  # terminal-green success is not overridden by struggle
    assert report.monitor.n_units == 1  # joined as a positive unit


def test_verdict_config_defaults_under_repo_logs(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    add_verdict_arguments(parser)
    config = verdict_config(parser.parse_args(["--repo", str(tmp_path), "--k", "3"]))
    assert config.k == 3
    assert config.retrieval_log == tmp_path.resolve() / ".thalamus" / "logs" / "retrieval.jsonl"
    assert config.trajectory_log == tmp_path.resolve() / ".thalamus" / "logs" / "trajectory.jsonl"


def test_compute_verdict_joins_recalls_to_outcomes() -> None:
    # s1: surfaced memory was used (utility 1.0) and tests passed (success)
    # s2: surfaced memory unused (utility 0.0) and tests failed (failure)
    events = [_event("e1", "s1", ["a"]), _event("e2", "s2", ["b"])]
    signals = [_signal("e1", "a", used=True), _signal("e2", "b", used=False)]
    trajectory = [_test_run("t1", "s1", failures=0), _test_run("t2", "s2", failures=1)]

    report = compute_verdict(events, signals, trajectory, k=5)

    assert report.utility.utility_at_k == 0.5  # mean(1.0, 0.0)
    assert report.n_tier1_sessions == 2
    assert report.n_tier2_sessions == 2
    assert report.monitor.n_units == 2
    assert report.monitor.alignment == 1.0  # success utility 1.0 - failure utility 0.0
    assert report.monitor.reward_hacking_suspected is False
    assert report.monitor_coverage == 1.0


def test_verdict_reports_usage_stability() -> None:
    # m1 reliably used across two sessions, m2 reliably ignored — the per-memory usefulness
    # signal rides along on the same logs (no failure/outcome label needed).
    events = [_event("e1", "s1", ["m1", "m2"]), _event("e2", "s2", ["m1", "m2"])]
    signals = [
        _signal("e1", "m1", used=True), _signal("e1", "m2", used=False),
        _signal("e2", "m1", used=True), _signal("e2", "m2", used=False),
    ]
    report = compute_verdict(events, signals, [], k=5)
    assert (report.usage.n_reliable, report.usage.n_ignored) == (1, 1)
    assert report.usage.separation == 1.0  # both memories cleanly classed
    assert report.usage.n_reused == 1  # only m1 was used across >= 2 sessions


def test_compute_verdict_excludes_sessions_without_an_outcome_label() -> None:
    # s2 has Tier-1 recalls but only a non-terminal/unknown outcome -> dropped from the join
    events = [_event("e1", "s1", ["a"]), _event("e2", "s2", ["b"])]
    signals = [_signal("e1", "a", used=True), _signal("e2", "b", used=True)]
    trajectory = [_test_run("t1", "s1", failures=0)]  # only s1 has a terminal outcome

    report = compute_verdict(events, signals, trajectory, k=5)

    assert report.n_tier1_sessions == 2
    assert report.n_tier2_sessions == 1  # only s1
    assert report.monitor.n_units == 1  # s2 dropped (missing truth)
    assert report.monitor_coverage == 0.5


def test_run_verdict_over_real_jsonl_logs_and_empty_is_honest(tmp_path: Path) -> None:
    logs = tmp_path / ".thalamus" / "logs"
    logs.mkdir(parents=True)

    # empty/missing logs: the loop is wired, but there is no data — must not crash
    parser = argparse.ArgumentParser()
    add_verdict_arguments(parser)
    empty = run_verdict(verdict_config(parser.parse_args(["--repo", str(tmp_path)])))
    assert empty.monitor.n_units == 0
    assert empty.utility.n_events == 0

    # now write real durable logs through the same sinks the serve path uses
    JsonlEventSink(logs / "retrieval.jsonl").emit(_event("e1", "s1", ["a"]))
    JsonlUsageSink(logs / "usage.jsonl").emit(_signal("e1", "a", used=True))
    JsonlTrajectorySink(logs / "trajectory.jsonl").emit(_test_run("t1", "s1", failures=0))

    report = run_verdict(verdict_config(parser.parse_args(["--repo", str(tmp_path)])))
    assert report.monitor.n_units == 1
    assert report.monitor.mean_utility_success == 1.0
