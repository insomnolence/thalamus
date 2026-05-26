from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core.types import EventId, RepoId, Scope, SessionId, TenantId
from thalamus.experiential import CommitBoundedSegmenter, SessionBoundedSegmenter
from thalamus.instrumentation import TrajectoryEvent, TrajectoryEventKind

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))


def _at(second: int) -> datetime:
    return datetime(2026, 5, 25, 12, 0, second, tzinfo=UTC)


def _session(session: str | None) -> SessionId | None:
    return None if session is None else SessionId(session)


def _commit(eid: str, second: int, sha: str, session: str | None = None) -> TrajectoryEvent:
    return TrajectoryEvent(
        EventId(eid), _at(second), SCOPE, TrajectoryEventKind.COMMIT,
        {"sha": sha, "subject": f"commit {sha}", "files": [f"{sha}.py"]},
        session_id=_session(session),
    )


def _test_run(
    eid: str, second: int, session: str | None = None, terminal: bool = False
) -> TrajectoryEvent:
    return TrajectoryEvent(
        EventId(eid), _at(second), SCOPE, TrajectoryEventKind.TEST_RUN,
        {"suite": "s", "tests": 1, "failures": 0, "errors": 0, "skipped": 0,
         "failed": [], "terminal": terminal},
        session_id=_session(session),
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


# --- S0 session-bounded segmentation ---------------------------------------------


def test_session_groups_events_by_session_id() -> None:
    events = [
        _commit("e1", 10, "A", session="s1"),
        _test_run("e2", 20, session="s2"),
        _commit("e3", 30, "B", session="s1"),
    ]
    spans = SessionBoundedSegmenter().segment(events)
    assert len(spans) == 2
    by_key = {span.key: span for span in spans}
    assert [e.event_id for e in by_key["session:s1"].events] == [EventId("e1"), EventId("e3")]
    assert [e.event_id for e in by_key["session:s2"].events] == [EventId("e2")]
    assert all(span.segmentation == "S0-session" for span in spans)


def test_session_excludes_unkeyed_events() -> None:
    # a commit with no session_id is missing data, not a synthetic catch-all episode
    spans = SessionBoundedSegmenter().segment(
        [_commit("e1", 10, "A", session="s1"), _commit("e2", 20, "B")]
    )
    assert len(spans) == 1
    assert spans[0].key == "session:s1"
    assert [e.event_id for e in spans[0].events] == [EventId("e1")]


def test_session_closed_only_with_a_terminal_signal() -> None:
    committed = SessionBoundedSegmenter().segment([_commit("e1", 10, "A", session="s1")])
    assert committed[0].closed is True
    terminal_test = SessionBoundedSegmenter().segment(
        [_test_run("e1", 10, session="s2", terminal=True)]
    )
    assert terminal_test[0].closed is True
    open_work = SessionBoundedSegmenter().segment([_test_run("e1", 10, session="s3")])
    assert open_work[0].closed is False


def test_session_spans_are_time_ordered_regardless_of_input() -> None:
    events = [_commit("e2", 40, "B", session="late"), _commit("e1", 10, "A", session="early")]
    spans = SessionBoundedSegmenter().segment(events)
    assert [span.key for span in spans] == ["session:early", "session:late"]


def test_session_empty_stream_yields_no_spans() -> None:
    assert SessionBoundedSegmenter().segment([]) == []
