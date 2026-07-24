"""Offline unit coverage for Neo4jStore control flow (no database required)."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock
from typing import Any

from thalamus.core.types import Hemisphere, RepoId, Scope, TenantId
from thalamus.store.neo4j_store import Neo4jStore

SCOPE = Scope(TenantId("t"), RepoId("r"))


def _row() -> dict[str, Any]:
    return {
        "memory_id": "m1",
        "hemisphere": Hemisphere.EXPERIENTIAL.value,
        "kind": "episode",
        "content": "one",
        "tenant_id": str(SCOPE.tenant_id),
        "repo_id": str(SCOPE.repo_id),
        "created_at": datetime(2026, 7, 23, tzinfo=UTC).isoformat(),
        "metadata_json": "{}",
        "score": 1.0,
    }


def _bare_store() -> Neo4jStore:
    store = object.__new__(Neo4jStore)
    store._dim = 2
    store._label = "M_experiential"
    store._index = "vec_experiential"
    store._listener_lock = RLock()
    store._listeners = []
    return store


def test_small_fully_indexed_scope_does_not_run_linear_fallback() -> None:
    store = _bare_store()
    calls: list[str] = []

    def run(cypher: str, **params: Any) -> list[Any]:
        del params
        calls.append(cypher)
        if "queryNodes" in cypher:
            return [_row()]
        if "count(m) AS total" in cypher:
            return [{"total": 1}]
        raise AssertionError("linear fallback should not run for a fully indexed small scope")

    store._run = run  # type: ignore[method-assign]
    results = store.search([1.0, 0.0], k=5, scope=SCOPE)
    assert len(results) == 1
    assert len(calls) == 2


def test_listener_registry_notifies_live_callbacks_and_prunes_dead_ones() -> None:
    store = _bare_store()
    calls: list[Scope] = []

    class Receiver:
        def notify(self, scope: Scope) -> None:
            calls.append(scope)

    live = Receiver()
    dead = Receiver()
    store.add_listener(live.notify)
    store.add_listener(dead.notify)
    del dead
    store._notify_listeners(SCOPE)
    assert calls == [SCOPE]
    assert len(store._listeners) == 1


def test_listener_registry_retains_standalone_callable() -> None:
    store = _bare_store()
    calls: list[Scope] = []
    store.add_listener(lambda scope: calls.append(scope))

    store._notify_listeners(SCOPE)
    assert calls == [SCOPE]
