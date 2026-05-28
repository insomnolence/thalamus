"""Neo4j belief-supersession integration tests (§13.18 R1).

ISOLATION (do not regress): these tests DELETE data, so they require a DISPOSABLE Neo4j via
``THALAMUS_TEST_NEO4J_URI`` — never the dogfood instance ``THALAMUS_NEO4J_URI`` serves.
Cleanup is also scoped to the test tenant (``t``). (Mirrors the structural Neo4j tests.)
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from thalamus.core.types import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.experiential import Neo4jSupersessionIndex
from thalamus.store import Neo4jStore, connect

_URI = os.environ.get("THALAMUS_TEST_NEO4J_URI")
pytestmark = pytest.mark.skipif(
    _URI is None,
    reason="set THALAMUS_TEST_NEO4J_URI (a DISPOSABLE Neo4j, never the dogfood instance)",
)
_TEST_TENANT = "t"
SCOPE = Scope(TenantId(_TEST_TENANT), RepoId("r"))
NOW = datetime(2026, 5, 27, tzinfo=UTC)
_DIM = 8


def _clean(driver: Any) -> None:
    with driver.session() as session:
        session.run("MATCH (m:M_experiential {tenant_id: $t}) DETACH DELETE m", t=_TEST_TENANT)


@pytest.fixture
def driver() -> Iterator[Any]:
    handle = connect(
        os.environ["THALAMUS_TEST_NEO4J_URI"],
        os.environ.get("THALAMUS_TEST_NEO4J_USER", "neo4j"),
        os.environ.get("THALAMUS_TEST_NEO4J_PASSWORD", ""),
    )
    _clean(handle)
    try:
        yield handle
    finally:
        _clean(handle)
        handle.close()


def _store(driver: Any) -> Neo4jStore:
    return Neo4jStore(
        dim=_DIM, driver=driver, hemisphere=Hemisphere.EXPERIENTIAL, encoder_id="deterministic"
    )


def _add(store: Neo4jStore, mid: str) -> MemoryRecord:
    record = MemoryRecord(
        MemoryId(mid), Hemisphere.EXPERIENTIAL, "decision", mid, SCOPE, NOW,
        metadata={"source": "curated"},
    )
    store.add(record, [0.1] * _DIM)
    return record


def test_supersession_edge_persists_and_reads_back(driver: Any) -> None:
    store = _store(driver)
    old = _add(store, "retained:old")
    new = _add(store, "retained:new")
    index = Neo4jSupersessionIndex(driver, SCOPE)

    index.supersede(old=old.ref, new=new.ref, reason="switched to Y because Z", at=NOW)

    view = index.superseded(SCOPE)
    assert set(view) == {old.ref}
    assert view[old.ref].superseded_by == MemoryId("retained:new")
    assert view[old.ref].reason == "switched to Y because Z"
    assert view[old.ref].at == NOW
    assert store.get(old.ref) is not None  # the old belief is kept, never deleted


def test_re_supersession_keeps_a_single_outgoing_edge(driver: Any) -> None:
    store = _store(driver)
    old = _add(store, "retained:old")
    second = _add(store, "retained:b")
    third = _add(store, "retained:c")
    index = Neo4jSupersessionIndex(driver, SCOPE)

    index.supersede(old=old.ref, new=second.ref, reason="first", at=NOW)
    index.supersede(old=old.ref, new=third.ref, reason="second", at=NOW)

    view = index.superseded(SCOPE)
    assert set(view) == {old.ref}
    assert view[old.ref].superseded_by == MemoryId("retained:c")
    assert view[old.ref].reason == "second"


def test_view_is_scope_isolated(driver: Any) -> None:
    store = _store(driver)
    old = _add(store, "retained:old")
    new = _add(store, "retained:new")
    Neo4jSupersessionIndex(driver, SCOPE).supersede(
        old=old.ref, new=new.ref, reason="r", at=NOW
    )
    other = Scope(TenantId(_TEST_TENANT), RepoId("other"))
    assert Neo4jSupersessionIndex(driver, other).superseded(other) == {}
