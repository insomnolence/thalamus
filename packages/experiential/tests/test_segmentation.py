from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core.types import EventId, RepoId, Scope, TenantId
from thalamus.experiential import CommitBoundedSegmenter
from thalamus.instrumentation import TrajectoryEvent, TrajectoryEventKind

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))


def _at(second: int) -> datetime:
    return datetime(2026, 5, 25, 12, 0, second, tzinfo=UTC)


def _commit(eid: str, second: int, sha: str) -> TrajectoryEvent:
    return TrajectoryEvent(
        EventId(eid), _at(second), SCOPE, TrajectoryEventKind.COMMIT,
        {"sha": sha, "subject": f"commit {sha}", "files": [f"{sha}.py"]},
    )


def _test_run(eid: str, second: int) -> TrajectoryEvent:
    return TrajectoryEvent(
        EventId(eid), _at(second), SCOPE, TrajectoryEventKind.TEST_RUN,
        {"suite": "s", "tests": 1, "failures": 0, "errors": 0, "skipped": 0, "failed": []},
    )


def test_each_commit_closes_an_episode() -> None:
    events = [
        _test_run("e1", 10), _commit("e2", 20, "A"), _test_run("e3", 30), _commit("e4", 40, "B"),
    ]
    spans = CommitBoundedSegmenter().segment(events)
    assert len(spans) == 2
    assert all(span.closed for span in spans)
    assert spans[0].events[-1].payload["sha"] == "A"
    assert [e.event_id for e in spans[0].events] == [EventId("e1"), EventId("e2")]


def test_trailing_uncommitted_work_is_one_open_span() -> None:
    events = [_commit("e1", 10, "A"), _test_run("e2", 20), _test_run("e3", 30)]
    spans = CommitBoundedSegmenter().segment(events)
    assert len(spans) == 2
    assert spans[0].closed is True
    assert spans[1].closed is False
    assert len(spans[1].events) == 2


def test_segmentation_sorts_by_timestamp_not_input_order() -> None:
    # fed out of order; the cut must follow time, not arrival
    events = [
        _commit("e2", 20, "A"), _test_run("e1", 10), _commit("e4", 40, "B"), _test_run("e3", 30),
    ]
    spans = CommitBoundedSegmenter().segment(events)
    assert [e.event_id for e in spans[0].events] == [EventId("e1"), EventId("e2")]
    assert [e.event_id for e in spans[1].events] == [EventId("e3"), EventId("e4")]


def test_empty_stream_yields_no_spans() -> None:
    assert CommitBoundedSegmenter().segment([]) == []
