"""Neo4jBehavioralStore integration tests (Track I / Architecture B).

ISOLATION (do not regress): these tests write + delete behavioral nodes, so they must NEVER run
against the dogfood/dev Neo4j. They require ``THALAMUS_TEST_NEO4J_URI`` — a DISPOSABLE instance,
never the one ``THALAMUS_NEO4J_URI`` serves. Cleanup is scoped to the test tenant as a second guard.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from thalamus.core.types import MemoryId, RepoId, Scope, SessionId, TenantId
from thalamus.experiential import Neo4jBehavioralStore
from thalamus.store import connect

_URI = os.environ.get("THALAMUS_TEST_NEO4J_URI")
_TEST_TENANT = "t1"
SCOPE = Scope(tenant_id=TenantId(_TEST_TENANT), repo_id=RepoId("r1"))

pytestmark = pytest.mark.skipif(
    _URI is None,
    reason="set THALAMUS_TEST_NEO4J_URI (a DISPOSABLE Neo4j, never the dogfood instance)",
)


def _clean(driver: object) -> None:
    with driver.session() as session:  # type: ignore[attr-defined]
        session.run("MATCH (b:M_behavioral_use {tenant_id: $t}) DETACH DELETE b", t=_TEST_TENANT)


@pytest.fixture
def store() -> Iterator[Neo4jBehavioralStore]:
    driver = connect(
        os.environ["THALAMUS_TEST_NEO4J_URI"],
        os.environ.get("THALAMUS_TEST_NEO4J_USER", "neo4j"),
        os.environ.get("THALAMUS_TEST_NEO4J_PASSWORD", ""),
    )
    _clean(driver)
    instance = Neo4jBehavioralStore(driver, SCOPE)
    try:
        yield instance
    finally:
        _clean(driver)
        instance.close()


def test_record_and_weights_count_distinct_sessions(store: Neo4jBehavioralStore) -> None:
    store.record_usage(
        {MemoryId("m-a"): {SessionId("s1"), SessionId("s2")}, MemoryId("m-b"): {SessionId("s1")}}
    )
    assert store.usage_weights() == {MemoryId("m-a"): 2.0, MemoryId("m-b"): 1.0}


def test_record_usage_is_idempotent_across_calls(store: Neo4jBehavioralStore) -> None:
    store.record_usage({MemoryId("m-a"): {SessionId("s1")}})
    store.record_usage({MemoryId("m-a"): {SessionId("s1"), SessionId("s2")}})  # s1 re-folded
    store.record_usage({MemoryId("m-a"): {SessionId("s1")}})  # whole thing again
    assert store.usage_weights() == {MemoryId("m-a"): 2.0}  # distinct + persisted, not doubled


def test_empty_update_is_a_noop(store: Neo4jBehavioralStore) -> None:
    store.record_usage({})
    assert store.usage_weights() == {}
