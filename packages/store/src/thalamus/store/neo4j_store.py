"""Neo4j-backed implementation of :class:`thalamus.core.Store`.

One graph, a **separate vector index per hemisphere** (the foundation decision):
each ``Neo4jStore`` owns one hemisphere's label + cosine vector index, so the
structural and experiential stores never pollute each other's retrieval while
sharing one database (the substrate for native cross-hemisphere links later).

(Referenced from Polynoica's ``memory/store/neo4j_store.py``: the
``CREATE VECTOR INDEX ... cosine`` + ``db.index.vector.queryNodes`` pattern is
kept; reimplemented to store ``MemoryRecord`` + ``Scope``, scope-filter the
search, and avoid torch.)

Requires the optional ``neo4j`` extra: ``pip install 'thalamus-store[neo4j]'``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from thalamus.core.exceptions import DimensionMismatchError, StoreError
from thalamus.core.types import (
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    ScoredMemory,
    TenantId,
    Vector,
)

if TYPE_CHECKING:
    from neo4j import Driver


def connect(uri: str, user: str, password: str) -> Driver:
    """Open a verified Neo4j driver (lazy import of the optional dependency)."""
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise StoreError(
            "Neo4j support requires the 'neo4j' extra: pip install 'thalamus-store[neo4j]'"
        ) from exc
    driver = GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    return driver


class Neo4jStore:
    """Per-hemisphere Neo4j store: record CRUD + native cosine vector search."""

    def __init__(
        self,
        dim: int,
        driver: Driver,
        hemisphere: Hemisphere,
        *,
        database: str = "neo4j",
    ) -> None:
        self._dim = dim
        self._driver = driver
        self._hemisphere = hemisphere
        self._database = database
        self._label = f"M_{hemisphere.value}"
        self._index = f"vec_{hemisphere.value}"
        self._ensure_index()

    def _run(self, cypher: str, **params: Any) -> list[Any]:
        try:
            with self._driver.session(database=self._database) as session:
                return list(session.run(cypher, **params))
        except Exception as exc:
            raise StoreError(f"Neo4j operation failed: {exc}") from exc

    def _ensure_index(self) -> None:
        # dim/label/index are trusted internal values (not user input) → safe to inline;
        # all record/query content flows through parameters.
        self._run(
            f"CREATE VECTOR INDEX {self._index} IF NOT EXISTS "
            f"FOR (m:{self._label}) ON (m.embedding) "
            f"OPTIONS {{indexConfig: {{`vector.dimensions`: {self._dim}, "
            f"`vector.similarity_function`: 'cosine'}}}}"
        )

    def _checked_vector(self, embedding: Vector, context: str) -> list[float]:
        vec = [float(value) for value in embedding]
        if len(vec) != self._dim:
            raise DimensionMismatchError(self._dim, len(vec), context)
        return vec

    def add(self, record: MemoryRecord, embedding: Vector) -> None:
        vec = self._checked_vector(embedding, "Neo4jStore.add")
        self._run(
            f"MERGE (m:{self._label} {{memory_id: $memory_id}}) "
            "SET m.hemisphere = $hemisphere, m.kind = $kind, m.content = $content, "
            "m.tenant_id = $tenant_id, m.repo_id = $repo_id, m.created_at = $created_at, "
            "m.metadata_json = $metadata_json, m.embedding = $embedding",
            memory_id=str(record.memory_id),
            hemisphere=record.hemisphere.value,
            kind=record.kind,
            content=record.content,
            tenant_id=str(record.scope.tenant_id),
            repo_id=str(record.scope.repo_id),
            created_at=record.created_at.isoformat(),
            metadata_json=json.dumps(dict(record.metadata)),
            embedding=vec,
        )

    def get(self, memory_id: MemoryId) -> MemoryRecord | None:
        rows = self._run(
            f"MATCH (m:{self._label} {{memory_id: $memory_id}}) RETURN m",
            memory_id=str(memory_id),
        )
        if not rows:
            return None
        return self._to_record(dict(rows[0]["m"]))

    def search(self, query: Vector, k: int, scope: Scope) -> list[ScoredMemory]:
        vec = self._checked_vector(query, "Neo4jStore.search")
        if k <= 0:
            return []
        # Vector index returns global top-N; over-fetch then scope-filter (Tier-0
        # exposure stays sound at small scale; native pre-filtering is a later upgrade).
        overfetch = max(k * 10, 50)
        rows = self._run(
            f"CALL db.index.vector.queryNodes('{self._index}', $overfetch, $vec) "
            "YIELD node, score "
            "WHERE node.tenant_id = $tenant_id AND node.repo_id = $repo_id "
            "RETURN node, score ORDER BY score DESC LIMIT $k",
            overfetch=overfetch,
            vec=vec,
            tenant_id=str(scope.tenant_id),
            repo_id=str(scope.repo_id),
            k=k,
        )
        results: list[ScoredMemory] = []
        for row in rows:
            score = float(row["score"])
            record = self._to_record(dict(row["node"]))
            results.append(ScoredMemory(record=record, score=score, features={"relevance": score}))
        return results

    def close(self) -> None:
        self._driver.close()

    @staticmethod
    def _to_record(props: Mapping[str, Any]) -> MemoryRecord:
        return MemoryRecord(
            memory_id=MemoryId(str(props["memory_id"])),
            hemisphere=Hemisphere(str(props["hemisphere"])),
            kind=str(props["kind"]),
            content=str(props["content"]),
            scope=Scope(TenantId(str(props["tenant_id"])), RepoId(str(props["repo_id"]))),
            created_at=datetime.fromisoformat(str(props["created_at"])),
            metadata=json.loads(str(props.get("metadata_json", "{}"))),
        )
