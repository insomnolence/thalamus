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
