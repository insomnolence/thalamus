from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core.types import EventId, MemoryId, MemoryRef, RepoId, Scope, TenantId
from thalamus.experiential import (
    EpisodeBuilder,
    EpisodeSpan,
    WhyProvenance,
    ingest_episodes,
)
from thalamus.instrumentation import TrajectoryEvent, TrajectoryEventKind
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))


def _at(second: int) -> datetime:
    return datetime(2026, 5, 25, 12, 0, second, tzinfo=UTC)


def _commit(eid: str, second: int, sha: str, subject: str, files: list[str]) -> TrajectoryEvent:
    return TrajectoryEvent(
        EventId(eid), _at(second), SCOPE, TrajectoryEventKind.COMMIT,
        {"sha": sha, "subject": subject, "files": files},
    )


def _failing_test(eid: str, second: int, test_id: str, message: str) -> TrajectoryEvent:
    return TrajectoryEvent(
        EventId(eid), _at(second), SCOPE, TrajectoryEventKind.TEST_RUN,
        {"suite": "s", "tests": 1, "failures": 1, "errors": 0, "skipped": 0,
         "failed": [{"id": test_id, "type": "failure", "message": message}]},
    )


def test_commit_span_becomes_episode_with_tagged_why() -> None:
    span = EpisodeSpan(
        events=(
            _failing_test("e1", 10, "tests/test_db.py::test_async", "blocking I/O in async loop"),
            _commit("e2", 20, "abc123", "use aiosqlite for the async store", ["store.py", "db.py"]),
        ),
        closed=True,
    )
    record = EpisodeBuilder().build(span)
    assert record is not None
    assert record.memory_id == MemoryId("episode:abc123")  # stable id from the commit sha
    assert record.kind == "episode"
    assert record.metadata["footprint"] == ["db.py", "store.py"]
    assert record.metadata["terminal_outcome"]["sha"] == "abc123"
    assert record.metadata["dead_ends"][0]["id"] == "tests/test_db.py::test_async"

    whys = {w["kind"]: w for w in record.metadata["why"]}
    assert whys["goal"]["provenance"] == WhyProvenance.ASSERTED  # commit subject = narrative
    assert whys["goal"]["text"] == "use aiosqlite for the async store"
    assert whys["rejected-alternative"]["provenance"] == WhyProvenance.EVIDENCED  # real dead-end
    # the embedded content carries the evidenced skeleton
    assert "aiosqlite" in record.content and "store.py" in record.content


def test_open_span_has_no_terminal_outcome() -> None:
    span = EpisodeSpan(events=(_failing_test("e1", 10, "t::x", "boom"),), closed=False)
    record = EpisodeBuilder().build(span)
    assert record is not None
    assert record.memory_id == MemoryId("episode:open:e1")
    assert record.metadata["terminal_outcome"] is None
    assert record.metadata["closed"] is False


def test_empty_span_builds_nothing() -> None:
    assert EpisodeBuilder().build(EpisodeSpan(events=(), closed=False)) is None


def test_build_is_idempotent_on_id() -> None:
    span = EpisodeSpan(events=(_commit("e1", 10, "abc", "x", ["a.py"]),), closed=True)
    assert EpisodeBuilder().build(span).memory_id == EpisodeBuilder().build(span).memory_id


def test_ingest_populates_brain1_and_reingest_is_idempotent() -> None:
    encoder = DeterministicEncoder(dim=64)
    store = InMemoryStore(dim=64)
    events = [
        _failing_test("e1", 10, "t::async", "blocking I/O"),
        _commit("e2", 20, "abc", "use aiosqlite for the async store", ["store.py"]),
        _commit("e3", 30, "def", "add the retry decorator", ["retry.py"]),
    ]
    records = ingest_episodes(events, encoder=encoder, store=store)
    assert len(records) == 2  # two commits -> two closed episodes
    assert len(store) == 2
    assert store.get(MemoryRef(SCOPE, MemoryId("episode:abc"))) is not None

    # the episode is retrievable by its content
    hits = store.search(encoder.encode(["aiosqlite async store"])[0], k=1, scope=SCOPE)
    assert hits[0].record.memory_id == MemoryId("episode:abc")

    # re-running over the same log refreshes in place, does not duplicate
    ingest_episodes(events, encoder=encoder, store=store)
    assert len(store) == 2
