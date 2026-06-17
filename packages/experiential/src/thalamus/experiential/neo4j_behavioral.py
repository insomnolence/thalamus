"""Neo4j-backed :class:`BehavioralStore` — the brain's durable usage record (Track I / B).

:class:`InMemoryBehavioralStore` is the v0 baseline; this persists the same accumulating
used-session sets in the shared graph, so the usage rung reads "how the brain has been used" *from
the brain* and the raw logs become a disposable write-ahead buffer.

Representation (graph-native, additive — never touches the ``M_experiential`` memory nodes): one
node per ``(memory_id, session_id)`` used-pair under a dedicated label, ``MERGE``-d so
``record_usage`` is idempotent at the database level (re-folding the same logs is a no-op, with no
read-modify-write race). The usage weight is then the ``count`` of distinct session nodes per memory
— exactly ``reuse_by_memory``'s quantity, now materialized. The driver is injected
(``thalamus.store.connect``) so this shares one connection + database with the store, the structural
graph, and the supersession index.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from thalamus.core.exceptions import StoreError
from thalamus.core.types import MemoryId, Scope, SessionId

if TYPE_CHECKING:
    from collections.abc import Set as AbstractSet

    from neo4j import Driver

_LABEL = "M_behavioral_use"  # one node per (memory_id, session_id) used-pair; additive


def _run(driver: Driver, database: str, cypher: str, **params: Any) -> list[Any]:
    try:
        with driver.session(database=database) as session:
            return list(session.run(cypher, **params))
    except Exception as exc:
        raise StoreError(f"Neo4j behavioral operation failed: {exc}") from exc


class Neo4jBehavioralStore:
    """``BehavioralStore`` as native per-``(memory, session)`` used-pair nodes in the graph."""

    def __init__(self, driver: Driver, scope: Scope, *, database: str = "neo4j") -> None:
        self._driver = driver
        self._scope = scope
        self._database = database
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        _run(
            self._driver,
            self._database,
            f"CREATE CONSTRAINT behavioral_use_unique IF NOT EXISTS FOR (b:{_LABEL}) "
            "REQUIRE (b.tenant_id, b.repo_id, b.memory_id, b.session_id) IS UNIQUE",
        )

    def record_usage(self, updates: Mapping[MemoryId, AbstractSet[SessionId]]) -> None:
        pairs = [
            {"memory_id": str(memory_id), "session_id": str(session_id)}
            for memory_id, sessions in updates.items()
            for session_id in sessions
        ]
        if not pairs:
            return
        _run(
            self._driver,
            self._database,
            f"UNWIND $pairs AS pair "
            f"MERGE (b:{_LABEL} {{tenant_id: $t, repo_id: $r, "
            f"memory_id: pair.memory_id, session_id: pair.session_id}})",
            pairs=pairs,
            t=str(self._scope.tenant_id),
            r=str(self._scope.repo_id),
        )

    def usage_weights(self) -> dict[MemoryId, float]:
        rows = _run(
            self._driver,
            self._database,
            f"MATCH (b:{_LABEL} {{tenant_id: $t, repo_id: $r}}) "
            "RETURN b.memory_id AS memory_id, count(DISTINCT b.session_id) AS sessions",
            t=str(self._scope.tenant_id),
            r=str(self._scope.repo_id),
        )
        return {MemoryId(str(row["memory_id"])): float(row["sessions"]) for row in rows}

    def close(self) -> None:
        self._driver.close()
