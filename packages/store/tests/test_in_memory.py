from __future__ import annotations

from datetime import UTC, datetime

import pytest
from thalamus.core.exceptions import DimensionMismatchError
from thalamus.core.types import (
    Hemisphere,
    MemoryId,
    MemoryRecord,
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
    assert store.get(MemoryId("m1")) is record
    assert store.get(MemoryId("missing")) is None
    assert len(store) == 1


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


def test_dim_mismatch() -> None:
    store = InMemoryStore(dim=3)
    with pytest.raises(DimensionMismatchError):
        store.add(_record("x"), [1.0, 0.0])
    with pytest.raises(DimensionMismatchError):
        store.search([1.0], k=1, scope=SCOPE)


def test_empty_store() -> None:
    assert InMemoryStore(dim=3).search([1.0, 0.0, 0.0], k=5, scope=SCOPE) == []
