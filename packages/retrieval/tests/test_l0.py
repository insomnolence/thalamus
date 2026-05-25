from __future__ import annotations

from datetime import UTC, datetime, timedelta

from thalamus.core.types import (
    Cue,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    TenantId,
)
from thalamus.retrieval import L0Retriever
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))
NOW = datetime(2026, 5, 24, tzinfo=UTC)


def _record(
    mid: str, content: str, *, created_at: datetime = NOW, importance: float = 0.0
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=MemoryId(mid),
        hemisphere=Hemisphere.EXPERIENTIAL,
        kind="episode",
        content=content,
        scope=SCOPE,
        created_at=created_at,
        metadata={"importance": importance},
    )


def _fixture() -> tuple[DeterministicEncoder, InMemoryStore]:
    encoder = DeterministicEncoder(dim=128)
    store = InMemoryStore(dim=128)
    return encoder, store


def _add(store: InMemoryStore, encoder: DeterministicEncoder, record: MemoryRecord) -> None:
    store.add(record, encoder.encode([record.content])[0])


def test_relevance_ranks_first() -> None:
    encoder, store = _fixture()
    _add(store, encoder, _record("sqlite", "switch the database to sqlite for simplicity"))
    _add(store, encoder, _record("async", "the async teardown is flaky in tests"))
    retriever = L0Retriever(encoder, store, w_recency=0.0, w_importance=0.0, now=lambda: NOW)
    result = retriever.retrieve(
        Cue(text="why did we switch the database to sqlite", scope=SCOPE), k=1
    )
    assert result.shown[0].record.memory_id == MemoryId("sqlite")
    assert len(result.shown) == 1
    # full candidate pool is retained for the logging contract, not just the top-k
    assert len(result.candidates) == 2


def test_recency_breaks_ties() -> None:
    encoder, store = _fixture()
    _add(store, encoder, _record("old", "duplicate note", created_at=NOW - timedelta(days=120)))
    _add(store, encoder, _record("new", "duplicate note", created_at=NOW))
    retriever = L0Retriever(encoder, store, w_recency=1.0, w_importance=0.0, now=lambda: NOW)
    result = retriever.retrieve(Cue(text="duplicate note", scope=SCOPE), k=2)
    assert result.shown[0].record.memory_id == MemoryId("new")


def test_importance_weights_in() -> None:
    encoder, store = _fixture()
    _add(store, encoder, _record("plain", "shared topic text", importance=0.0))
    _add(store, encoder, _record("important", "shared topic text", importance=1.0))
    retriever = L0Retriever(encoder, store, w_recency=0.0, w_importance=5.0, now=lambda: NOW)
    result = retriever.retrieve(Cue(text="shared topic text", scope=SCOPE), k=2)
    assert result.shown[0].record.memory_id == MemoryId("important")


def test_precomputed_embedding_path() -> None:
    encoder, store = _fixture()
    _add(store, encoder, _record("only", "the only memory"))
    embedding = encoder.encode(["the only memory"])[0]
    retriever = L0Retriever(encoder, store, now=lambda: NOW)
    result = retriever.retrieve(
        Cue(text="text is ignored when embedding is supplied", scope=SCOPE, embedding=embedding),
        k=1,
    )
    assert [s.record.memory_id for s in result.shown] == [MemoryId("only")]
