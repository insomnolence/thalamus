from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from thalamus.core.exceptions import DimensionMismatchError
from thalamus.core.types import (
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    ScoredMemory,
    TenantId,
)
from thalamus.store import Neo4jStore, connect

_URI = os.environ.get("THALAMUS_NEO4J_URI")
SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))
OTHER = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r2"))

pytestmark = pytest.mark.skipif(
    _URI is None, reason="set THALAMUS_NEO4J_URI to run Neo4j integration tests"
)


def _record(mid: str, content: str, scope: Scope = SCOPE) -> MemoryRecord:
    return MemoryRecord(
        memory_id=MemoryId(mid),
        hemisphere=Hemisphere.EXPERIENTIAL,
        kind="episode",
        content=content,
        scope=scope,
        created_at=datetime(2026, 5, 24, tzinfo=UTC),
        metadata={"k": "v"},
    )


@pytest.fixture
def store() -> Iterator[Neo4jStore]:
    uri = os.environ["THALAMUS_NEO4J_URI"]
    user = os.environ.get("THALAMUS_NEO4J_USER", "neo4j")
    password = os.environ.get("THALAMUS_NEO4J_PASSWORD", "")
    driver = connect(uri, user, password)
    with driver.session() as session:
        session.run("MATCH (m:M_experiential) DETACH DELETE m")
    instance = Neo4jStore(dim=4, driver=driver, hemisphere=Hemisphere.EXPERIENTIAL)
    try:
        yield instance
    finally:
        with driver.session() as session:
            session.run("MATCH (m:M_experiential) DETACH DELETE m")
        instance.close()


def _search_until(store: Neo4jStore, vec: list[float], expected: int) -> list[ScoredMemory]:
    # The vector index populates asynchronously after writes; poll briefly.
    for _ in range(40):
        results = store.search(vec, k=10, scope=SCOPE)
        if len(results) >= expected:
            return results
        time.sleep(0.25)
    return store.search(vec, k=10, scope=SCOPE)


def test_add_get_roundtrip(store: Neo4jStore) -> None:
    store.add(_record("m1", "hello world"), [1.0, 0.0, 0.0, 0.0])
    got = store.get(MemoryId("m1"))
    assert got is not None
    assert got.content == "hello world"
    assert got.metadata == {"k": "v"}
    assert got.scope == SCOPE
    assert store.get(MemoryId("missing")) is None


def test_vector_search_orders_and_scopes(store: Neo4jStore) -> None:
    store.add(_record("near", "near"), [1.0, 0.0, 0.0, 0.0])
    store.add(_record("far", "far"), [0.0, 1.0, 0.0, 0.0])
    store.add(_record("other", "other", OTHER), [1.0, 0.0, 0.0, 0.0])
    results = _search_until(store, [1.0, 0.0, 0.0, 0.0], expected=2)
    ids = [r.record.memory_id for r in results]
    assert ids[0] == MemoryId("near")  # most similar ranks first
    assert MemoryId("far") in ids
    assert MemoryId("other") not in ids  # scope isolation


def test_dim_mismatch(store: Neo4jStore) -> None:
    with pytest.raises(DimensionMismatchError):
        store.add(_record("x", "x"), [1.0, 2.0])
    with pytest.raises(DimensionMismatchError):
        store.search([1.0], k=1, scope=SCOPE)
