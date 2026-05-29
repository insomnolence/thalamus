from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core.types import EventId, RepoId, Scope, TenantId
from thalamus.experiential import EpisodeBuilder, EpisodeOutcome, EpisodeSpan, classify_outcome
from thalamus.experiential.outcome import is_success
from thalamus.instrumentation import TrajectoryEvent, TrajectoryEventKind

S = Scope(tenant_id=TenantId("t"), repo_id=RepoId("r"))


def _at(second: int) -> datetime:
    return datetime(2026, 5, 25, 12, 0, second, tzinfo=UTC)


def _commit(second: int) -> TrajectoryEvent:
    return TrajectoryEvent(
        EventId(f"c{second}"), _at(second), S, TrajectoryEventKind.COMMIT,
        {"sha": f"s{second}", "subject": "x", "files": []},
    )


def _test_run(second: int, failures: int) -> TrajectoryEvent:
    return TrajectoryEvent(
        EventId(f"t{second}"), _at(second), S, TrajectoryEventKind.TEST_RUN,
        {"failures": failures, "errors": 0, "failed": [], "terminal": True},
    )


def _revert(second: int) -> TrajectoryEvent:
    return TrajectoryEvent(EventId(f"r{second}"), _at(second), S, TrajectoryEventKind.REVERT, {})


def _live_test(second: int, failures: int) -> TrajectoryEvent:
    """A live-captured test run — NOT marked terminal (the pytest-plugin shape)."""
    return TrajectoryEvent(
        EventId(f"L{second}"), _at(second), S, TrajectoryEventKind.TEST_RUN,
        {"failures": failures, "errors": 0, "failed": [], "terminal": False},
    )


def test_classify_outcomes() -> None:
    out = classify_outcome
    # last test run green dominates -> passed (fed out of order to check sorting)
    assert out([_test_run(30, 0), _commit(20), _test_run(10, 1)]) == EpisodeOutcome.PASSED
    assert out([_commit(10), _test_run(20, 2)]) == EpisodeOutcome.FAILED
    assert out([_commit(10)]) == EpisodeOutcome.COMMITTED
    prior_failure = TrajectoryEvent(
        EventId("pre"),
        _at(5),
        S,
        TrajectoryEventKind.TEST_RUN,
        {"failures": 1, "errors": 0, "failed": [], "terminal": False},
    )
    assert out([prior_failure, _commit(10)]) == EpisodeOutcome.COMMITTED
    assert out([_commit(10), _revert(20)]) == EpisodeOutcome.REVERTED
    assert out([_revert(10), _commit(20)]) == EpisodeOutcome.COMMITTED  # revert precedes commit
    assert out([]) == EpisodeOutcome.OPEN


def test_classify_outcome_blesses_green_test_before_commit() -> None:
    """Option B: a commit blesses the last green (non-terminal) test run before it as PASSED,
    so the real green-tests-then-commit flow yields a Tier-2 success — but a stale non-terminal
    failure is not trusted as FAILED."""
    out = classify_outcome
    # green live test then commit -> PASSED (the common dogfood flow)
    assert out([_live_test(5, 0), _commit(10)]) == EpisodeOutcome.PASSED
    assert out([_commit(10), _live_test(5, 0)]) == EpisodeOutcome.PASSED  # order-independent
    # failed early, fixed (green) then commit -> last green pre-commit run -> PASSED
    assert out([_live_test(3, 2), _live_test(6, 0), _commit(10)]) == EpisodeOutcome.PASSED
    # green then a later failing run then commit -> last pre-commit run is red -> not trusted
    assert out([_live_test(3, 0), _live_test(6, 1), _commit(10)]) == EpisodeOutcome.COMMITTED
    # a green live test only AFTER the commit doesn't count (needs the terminal flag) -> COMMITTED
    assert out([_commit(5), _live_test(10, 0)]) == EpisodeOutcome.COMMITTED
    # no commit at all -> not blessed -> OPEN
    assert out([_live_test(5, 0)]) == EpisodeOutcome.OPEN


def test_is_success_maps_unknown_to_none() -> None:
    assert is_success(EpisodeOutcome.PASSED) is True
    assert is_success(EpisodeOutcome.COMMITTED) is None
    assert is_success(EpisodeOutcome.FAILED) is False
    assert is_success(EpisodeOutcome.REVERTED) is False
    assert is_success(EpisodeOutcome.OPEN) is None  # excluded, not counted as failure


def test_episode_records_its_outcome() -> None:
    record = EpisodeBuilder().build(EpisodeSpan(events=(_commit(10),), closed=True))
    assert record is not None
    assert record.metadata["outcome"] == EpisodeOutcome.COMMITTED


# End-to-end: a jest run (captured as a terminal aggregate JUnit report) produces a real
# Tier-2 *negative* label — the costly-to-fake outcome signal, not just green passes.
_JEST_RED = """<?xml version="1.0"?>
<testsuites name="jest tests" tests="2" failures="1" errors="0">
  <testsuite name="src/a.test.ts" tests="1" failures="0" errors="0" skipped="0">
    <testcase classname="a" name="ok"/>
  </testsuite>
  <testsuite name="src/b.test.ts" tests="1" failures="1" errors="0" skipped="0">
    <testcase classname="b" name="bad"><failure message="boom">trace</failure></testcase>
  </testsuite>
</testsuites>
"""

_JEST_GREEN = """<?xml version="1.0"?>
<testsuites name="jest tests" tests="2" failures="0" errors="0">
  <testsuite name="src/a.test.ts" tests="1" failures="0" errors="0" skipped="0">
    <testcase classname="a" name="ok"/>
  </testsuite>
  <testsuite name="src/b.test.ts" tests="1" failures="0" errors="0" skipped="0">
    <testcase classname="b" name="also-ok"/>
  </testsuite>
</testsuites>
"""


def _jest_outcome(tmp_path, xml: str) -> EpisodeOutcome:  # noqa: ANN001 - test helper
    from pathlib import Path

    from thalamus.instrumentation import JUnitObserver

    report = Path(tmp_path) / "junit.xml"
    report.write_text(xml, encoding="utf-8")
    events = JUnitObserver(S).ingest(report, terminal=True, aggregate=True)
    return classify_outcome(events)


def test_failing_jest_run_is_a_failed_tier2_label(tmp_path) -> None:  # noqa: ANN001
    outcome = _jest_outcome(tmp_path, _JEST_RED)
    assert outcome is EpisodeOutcome.FAILED
    assert is_success(outcome) is False  # a real negative outcome — the discriminating signal


def test_green_jest_run_is_a_passed_tier2_label(tmp_path) -> None:  # noqa: ANN001
    outcome = _jest_outcome(tmp_path, _JEST_GREEN)
    assert outcome is EpisodeOutcome.PASSED
    assert is_success(outcome) is True
