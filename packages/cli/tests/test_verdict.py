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
    assert report.n_reverted_sessions == 1
    assert report.monitor.n_units == 1  # the fate negative created a joinable unit
    assert report.monitor_without_fate.n_units == 0  # classify alone labelled nothing

    # Without a matching revert, the same session yields no Tier-2 label (COMMITTED → excluded).
    clean = compute_verdict(events, signals, trajectory, k=5, reverted=frozenset())
    assert clean.n_reverted_sessions == 0
    assert clean.monitor.n_units == 0


def _test_run(eid: str, session: str, *, failures: int) -> object:
    return build_test_run_event(
        event_id=EventId(eid), timestamp=NOW, scope=SCOPE, tests=1, failures=failures,
        errors=0, skipped=0, failed=[], terminal=True, session_id=SessionId(session),
    )


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
