from __future__ import annotations

from datetime import UTC, datetime
from threading import Barrier, BrokenBarrierError, Event, Thread

import pytest
from thalamus.core.types import (
    Cue,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    TenantId,
)
from thalamus.retrieval import LexicalRetriever, bm25_scores, tokenize
from thalamus.store import InMemoryStore

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))
SCOPE_TWO = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r2"))
NOW = datetime(2026, 6, 14, tzinfo=UTC)


def _record(mid: str, content: str, *, scope: Scope = SCOPE) -> MemoryRecord:
    return MemoryRecord(
        memory_id=MemoryId(mid),
        hemisphere=Hemisphere.EXPERIENTIAL,
        kind="episode",
        content=content,
        scope=scope,
        created_at=NOW,
    )


def _store(*records: MemoryRecord) -> InMemoryStore:
    store = InMemoryStore(dim=8)
    for record in records:
        store.add(record, [0.0] * 8)  # lexical retrieval never reads the vector
    return store


def test_tokenize_keeps_identifiers_drops_stopwords() -> None:
    assert tokenize("the build_corpora helper is in BrainTwo") == [
        "build_corpora",
        "helper",
        "braintwo",
    ]


def test_bm25_ranks_the_exact_term_hit_first() -> None:
    store = _store(
        _record("hit", "the StructuralRederivePass re-derives Brain 2 on the maintenance tick"),
        _record("near", "the dreaming scheduler refreshes the gateway's derived views"),
        _record("off", "completely unrelated note about coffee and weather"),
    )
    result = LexicalRetriever(store).retrieve(
        Cue(text="where is StructuralRederivePass", scope=SCOPE), k=3
    )
    assert result.shown[0].record.memory_id == MemoryId("hit")


def test_no_term_overlap_is_excluded() -> None:
    store = _store(
        _record("a", "alpha beta gamma"),
        _record("b", "delta epsilon zeta"),
    )
    result = LexicalRetriever(store).retrieve(Cue(text="gamma", scope=SCOPE), k=5)
    # only the doc that actually contains a query term is scored/returned
    assert [s.record.memory_id for s in result.candidates] == [MemoryId("a")]


def test_rarer_term_outweighs_a_common_one() -> None:
    # "the" is a stop word and "config" is common across docs; "regen_command" is rare -> the doc
    # that has the rare term should win even though both share the common term.
    store = _store(
        _record("common", "the config the config the config the config the config"),
        _record("rare", "the config mentions regen_command exactly once here"),
    )
    result = LexicalRetriever(store).retrieve(
        Cue(text="config regen_command", scope=SCOPE), k=2
    )
    assert result.shown[0].record.memory_id == MemoryId("rare")


def test_store_add_invalidates_lexical_cache() -> None:
    store = InMemoryStore(dim=8)
    store.add(_record("first", "apple orange banana"), [0.0] * 8)
    retriever = LexicalRetriever(store)

    # First retrieval populates the scope index cache
    res1 = retriever.retrieve(Cue(text="cherry", scope=SCOPE), k=5)
    assert len(res1.shown) == 0

    # Adding a new memory triggers store listener -> invalidates lexical cache
    store.add(_record("second", "cherry kiwi grape"), [0.0] * 8)

    # Second retrieval observes the newly added memory immediately
    res2 = retriever.retrieve(Cue(text="cherry", scope=SCOPE), k=5)
    assert len(res2.shown) == 1
    assert res2.shown[0].record.memory_id == MemoryId("second")


def test_precomputed_index_matches_public_bm25_reference_with_empty_doc() -> None:
    records = (
        _record("alpha", "alpha alpha beta"),
        _record("empty", "the and of"),
        _record("beta", "alpha beta gamma delta"),
    )
    query = tokenize("alpha beta")
    expected = bm25_scores(
        query,
        [(record.memory_id, tokenize(record.content)) for record in records],
        k1=1.5,
        b=0.75,
    )
    result = LexicalRetriever(_store(*records)).retrieve(
        Cue(text="alpha beta", scope=SCOPE), k=10
    )
    actual = {item.record.memory_id: item.score for item in result.candidates}
    assert actual == pytest.approx(expected)


def test_write_during_cold_index_build_cannot_lose_invalidation() -> None:
    class BlockingScanStore(InMemoryStore):
        def __init__(self) -> None:
            super().__init__(dim=8)
            self.snapshot_ready = Event()
            self.release_snapshot = Event()
            self.block_once = True

        def scan(self, scope: Scope) -> list[MemoryRecord]:
            records = super().scan(scope)
            if self.block_once:
                self.block_once = False
                self.snapshot_ready.set()
                assert self.release_snapshot.wait(timeout=5)
            return records

    store = BlockingScanStore()
    store.add(_record("first", "apple orange"), [0.0] * 8)
    retriever = LexicalRetriever(store)
    reader = Thread(target=lambda: retriever.retrieve(Cue(text="cherry", scope=SCOPE), k=5))
    reader.start()
    assert store.snapshot_ready.wait(timeout=5)

    writer = Thread(target=lambda: store.add(_record("second", "cherry kiwi"), [0.0] * 8))
    writer.start()
    store.release_snapshot.set()
    reader.join(timeout=5)
    writer.join(timeout=5)
    assert not reader.is_alive()
    assert not writer.is_alive()

    result = retriever.retrieve(Cue(text="cherry", scope=SCOPE), k=5)
    assert [item.record.memory_id for item in result.shown] == [MemoryId("second")]


def test_cold_indexes_for_independent_scopes_build_concurrently() -> None:
    class ConcurrentScanStore(InMemoryStore):
        def __init__(self) -> None:
            super().__init__(dim=8)
            self.scans_met = Barrier(2)

        def scan(self, scope: Scope) -> list[MemoryRecord]:
            try:
                self.scans_met.wait(timeout=1.0)
            except BrokenBarrierError as exc:
                raise AssertionError("cold scans were serialized by the retriever lock") from exc
            return super().scan(scope)

    store = ConcurrentScanStore()
    store.add(_record("one", "alpha", scope=SCOPE), [0.0] * 8)
    store.add(_record("two", "beta", scope=SCOPE_TWO), [0.0] * 8)
    retriever = LexicalRetriever(store)
    errors: list[BaseException] = []
    shown: dict[Scope, list[MemoryId]] = {}

    def retrieve(scope: Scope, query: str) -> None:
        try:
            result = retriever.retrieve(Cue(text=query, scope=scope), k=1)
            shown[scope] = [item.record.memory_id for item in result.shown]
        except BaseException as exc:
            errors.append(exc)

    readers = [
        Thread(target=retrieve, args=(SCOPE, "alpha")),
        Thread(target=retrieve, args=(SCOPE_TWO, "beta")),
    ]
    for reader in readers:
        reader.start()
    for reader in readers:
        reader.join(timeout=3.0)

    assert all(not reader.is_alive() for reader in readers)
    assert errors == []
    assert shown == {SCOPE: [MemoryId("one")], SCOPE_TWO: [MemoryId("two")]}


def test_sustained_invalidations_bound_cold_build_retries() -> None:
    class ChurningScanStore(InMemoryStore):
        def __init__(self) -> None:
            super().__init__(dim=8)
            self.scan_calls = 0
            self.on_scan = lambda scope: None

        def scan(self, scope: Scope) -> list[MemoryRecord]:
            records = super().scan(scope)
            self.scan_calls += 1
            if self.scan_calls <= 10:
                self.on_scan(scope)
            return records

    store = ChurningScanStore()
    store.add(_record("one", "alpha"), [0.0] * 8)
    retriever = LexicalRetriever(store)
    store.on_scan = retriever.invalidate

    result = retriever.retrieve(Cue(text="alpha", scope=SCOPE), k=1)

    assert [item.record.memory_id for item in result.shown] == [MemoryId("one")]
    assert store.scan_calls == 3
