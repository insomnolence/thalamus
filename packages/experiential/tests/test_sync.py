from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.types import EventId, MemoryId, RepoId, Scope, TenantId
from thalamus.experiential import (
    FileCheckpoint,
    GitEpisodeIngestor,
    InMemoryCheckpoint,
)
from thalamus.instrumentation import TrajectoryEvent, TrajectoryEventKind
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
