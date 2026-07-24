from __future__ import annotations

from datetime import UTC, datetime

import pytest
from thalamus.core.exceptions import DimensionMismatchError
from thalamus.core.types import (
    Hemisphere,
    MemoryId,
    MemoryRecord,
    MemoryRef,
    RepoId,
    Scope,
    TenantId,
)
from thalamus.store import InMemoryStore

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))
OTHER = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r2"))


def _record(mid: str, scope: Scope = SCOPE) -> MemoryRecord:
    return MemoryRecord(
        memory_id=MemoryId(mid),
        hemisphere=Hemisphere.EXPERIENTIAL,
        kind="episode",
        content=f"content {mid}",
        scope=scope,
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
    )


def test_add_get() -> None:
    store = InMemoryStore(dim=3)
    record = _record("m1")
    store.add(record, [1.0, 0.0, 0.0])
    assert store.get(MemoryRef(SCOPE, MemoryId("m1"))) is record
    assert store.get(MemoryRef(SCOPE, MemoryId("missing"))) is None
    assert len(store) == 1


def test_get_many() -> None:
    store = InMemoryStore(dim=3)
    r1, r2 = _record("m1"), _record("m2")
    store.add(r1, [1.0, 0.0, 0.0])
    store.add(r2, [0.0, 1.0, 0.0])
    refs = [
        MemoryRef(SCOPE, MemoryId("m1")),
        MemoryRef(SCOPE, MemoryId("m2")),
        MemoryRef(SCOPE, MemoryId("m3")),
    ]
    res = store.get_many(refs)
    assert res == {MemoryRef(SCOPE, MemoryId("m1")): r1, MemoryRef(SCOPE, MemoryId("m2")): r2}


def test_search_orders_by_similarity() -> None:
    store = InMemoryStore(dim=3)
    store.add(_record("near"), [1.0, 0.0, 0.0])
    store.add(_record("far"), [0.0, 1.0, 0.0])
    results = store.search([1.0, 0.0, 0.0], k=2, scope=SCOPE)
    assert [r.record.memory_id for r in results] == [MemoryId("near"), MemoryId("far")]
    assert results[0].score == pytest.approx(1.0)


def test_scope_isolation() -> None:
    store = InMemoryStore(dim=2)
    store.add(_record("mine", SCOPE), [1.0, 0.0])
    store.add(_record("theirs", OTHER), [1.0, 0.0])
    results = store.search([1.0, 0.0], k=5, scope=SCOPE)
    assert [r.record.memory_id for r in results] == [MemoryId("mine")]


def test_identical_memory_ids_coexist_across_scopes() -> None:
    store = InMemoryStore(dim=2)
    store.add(_record("same", SCOPE), [1.0, 0.0])
    store.add(_record("same", OTHER), [0.0, 1.0])
    assert store.get(MemoryRef(SCOPE, MemoryId("same"))).scope == SCOPE
    assert store.get(MemoryRef(OTHER, MemoryId("same"))).scope == OTHER


def test_scan_returns_scoped_records() -> None:
    store = InMemoryStore(dim=2)
    store.add(_record("a", SCOPE), [1.0, 0.0])
    store.add(_record("b", SCOPE), [0.0, 1.0])
    store.add(_record("other", OTHER), [1.0, 0.0])
    scanned = {r.memory_id for r in store.scan(SCOPE)}
    assert scanned == {MemoryId("a"), MemoryId("b")}  # scoped, not query-driven
    assert store.scan(OTHER) == [_record("other", OTHER)]


def test_scan_with_embeddings_returns_records_and_vectors() -> None:
    store = InMemoryStore(dim=2)
    store.add(_record("a", SCOPE), [1.0, 0.0])
    store.add(_record("b", SCOPE), [0.0, 1.0])
    store.add(_record("other", OTHER), [1.0, 0.0])
    pairs = {r.memory_id: emb for r, emb in store.scan_with_embeddings(SCOPE)}
    assert pairs == {MemoryId("a"): (1.0, 0.0), MemoryId("b"): (0.0, 1.0)}  # scoped, with vectors


def test_dim_mismatch() -> None:
    store = InMemoryStore(dim=3)
    with pytest.raises(DimensionMismatchError):
        store.add(_record("x"), [1.0, 0.0])
    with pytest.raises(DimensionMismatchError):
        store.search([1.0], k=1, scope=SCOPE)


def test_empty_store() -> None:
    assert InMemoryStore(dim=3).search([1.0, 0.0, 0.0], k=5, scope=SCOPE) == []


def test_add_listener_weakref() -> None:
    store = InMemoryStore(dim=2)

    class Receiver:
        def __init__(self) -> None:
            self.calls: list[Scope] = []

        def on_write(self, s: Scope) -> None:
            self.calls.append(s)

    r = Receiver()
    store.add_listener(r.on_write)
    store.add(_record("a", SCOPE), [1.0, 0.0])
    assert r.calls == [SCOPE]

    # Del Receiver instance, next write purges dead weakref without error
    del r
    store.add(_record("b", SCOPE), [0.0, 1.0])


def test_add_listener_retains_standalone_callable() -> None:
    store = InMemoryStore(dim=2)
    calls: list[Scope] = []
    store.add_listener(lambda scope: calls.append(scope))

    store.add(_record("a", SCOPE), [1.0, 0.0])
    assert calls == [SCOPE]
