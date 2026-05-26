from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.types import EventId, MemoryId, RepoId, Scope, SessionId, TenantId
from thalamus.experiential import (
    FileCheckpoint,
    GitEpisodeIngestor,
    InMemoryCheckpoint,
    SessionStampingSource,
)
from thalamus.instrumentation import (
    SessionContext,
    SessionContextStore,
    TrajectoryEvent,
    TrajectoryEventKind,
)
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))


def _commit(eid: str, second: int, sha: str) -> TrajectoryEvent:
    return TrajectoryEvent(
        EventId(eid), datetime(2026, 5, 25, 12, 0, second, tzinfo=UTC), SCOPE,
        TrajectoryEventKind.COMMIT, {"sha": sha, "subject": f"work {sha}", "files": [f"{sha}.py"]},
    )


class _FakeSource:
    """Returns all commits until the cursor reaches the newest sha, then nothing —
    mimics a repo that gets no new commits between syncs."""

    def __init__(self, events: list[TrajectoryEvent]) -> None:
        self._events = events

    def poll(self, since: str | None = None) -> list[TrajectoryEvent]:
        if since == self._events[-1].payload["sha"]:
            return []
        return list(self._events)


def _ingestor(source: _FakeSource, checkpoint: InMemoryCheckpoint) -> GitEpisodeIngestor:
    return GitEpisodeIngestor(
        source,
        encoder=DeterministicEncoder(dim=64),
        store=InMemoryStore(dim=64),
        checkpoint=checkpoint,
    )


def test_sync_ingests_and_advances_checkpoint() -> None:
    source = _FakeSource([_commit("e1", 10, "aaa"), _commit("e2", 20, "bbb")])
    checkpoint = InMemoryCheckpoint()
    ingestor = _ingestor(source, checkpoint)

    records = ingestor.sync()
    assert {r.memory_id for r in records} == {MemoryId("episode:aaa"), MemoryId("episode:bbb")}
    assert checkpoint.load() == "bbb"  # advanced to the newest ingested commit


def test_second_sync_is_a_noop_when_nothing_new() -> None:
    source = _FakeSource([_commit("e1", 10, "aaa"), _commit("e2", 20, "bbb")])
    store = InMemoryStore(dim=64)
    ingestor = GitEpisodeIngestor(
        source, encoder=DeterministicEncoder(dim=64), store=store, checkpoint=InMemoryCheckpoint()
    )
    ingestor.sync()
    assert len(store) == 2
    assert ingestor.sync() == []  # checkpoint now at newest -> source returns nothing
    assert len(store) == 2


def test_file_checkpoint_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "state" / "git.cursor"
    cp = FileCheckpoint(path)
    assert cp.load() is None
    cp.save("deadbeef")
    assert FileCheckpoint(path).load() == "deadbeef"  # a fresh instance reads it back


# --- SessionStampingSource: join out-of-band commits to the active serve session ---


class _Sessions(SessionContextStore):
    def __init__(self, ctx: SessionContext | None) -> None:
        self._ctx = ctx

    def publish(self, ctx: SessionContext) -> None:
        self._ctx = ctx

    def read(self) -> SessionContext | None:
        return self._ctx


def _ctx(session: str, started_second: int) -> SessionContext:
    return SessionContext(
        SessionId(session), datetime(2026, 5, 25, 12, 0, started_second, tzinfo=UTC)
    )


_AT_59 = lambda: datetime(2026, 5, 25, 12, 0, 59, tzinfo=UTC)  # noqa: E731 (test clock)


def test_stamps_commit_within_the_session_window() -> None:
    source = SessionStampingSource(
        _FakeSource([_commit("e1", 30, "aaa")]), _Sessions(_ctx("sess-1", 10)), now=_AT_59
    )
    (event,) = source.poll()
    assert event.session_id == SessionId("sess-1")


def test_does_not_stamp_a_commit_from_before_the_session_started() -> None:
    # session started at :10, commit happened at :05 — not this session's work
    source = SessionStampingSource(
        _FakeSource([_commit("e1", 5, "aaa")]), _Sessions(_ctx("sess-1", 10)), now=_AT_59
    )
    (event,) = source.poll()
    assert event.session_id is None  # missing over wrong


def test_passes_through_when_no_session_is_published() -> None:
    source = SessionStampingSource(_FakeSource([_commit("e1", 30, "aaa")]), _Sessions(None))
    (event,) = source.poll()
    assert event.session_id is None


def test_never_overwrites_an_already_keyed_event() -> None:
    keyed = replace(_commit("e1", 30, "aaa"), session_id=SessionId("explicit"))
    source = SessionStampingSource(
        _FakeSource([keyed]), _Sessions(_ctx("sess-1", 10)), now=_AT_59
    )
    (event,) = source.poll()
    assert event.session_id == SessionId("explicit")
