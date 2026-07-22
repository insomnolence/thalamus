"""Neo4j-backed implementation of :class:`thalamus.core.Store`.

One graph, a **separate vector index per hemisphere** (the foundation decision):
each ``Neo4jStore`` owns one hemisphere's label + cosine vector index, so the
structural and experiential stores never pollute each other's retrieval while
sharing one database (the substrate for native cross-hemisphere links later).

(Referenced from an earlier project of ours: the
``CREATE VECTOR INDEX ... cosine`` + ``db.index.vector.queryNodes`` pattern is
kept; reimplemented to store ``MemoryRecord`` + ``Scope``, scope-filter the
search, and avoid torch.)

Requires the optional ``neo4j`` extra: ``pip install 'thalamus-store[neo4j]'``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from thalamus.core.exceptions import DimensionMismatchError, StoreError
from thalamus.core.types import (
    Hemisphere,
    MemoryId,
    MemoryRecord,
    MemoryRef,
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
        encoder_id: str | None = None,
    ) -> None:
        self._dim = dim
        self._driver = driver
        self._hemisphere = hemisphere
        self._database = database
        self._label = f"M_{hemisphere.value}"
        self._index = f"vec_{hemisphere.value}"
        self._ensure_index()
        if encoder_id is not None:
            self._ensure_encoder_config(encoder_id)

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
        self._run(
            f"CREATE CONSTRAINT memory_scope_{self._hemisphere.value} IF NOT EXISTS "
            f"FOR (m:{self._label}) REQUIRE (m.tenant_id, m.repo_id, m.memory_id) IS UNIQUE"
        )
        self._run(
            f"CREATE INDEX memory_scope_lookup_{self._hemisphere.value} IF NOT EXISTS "
            f"FOR (m:{self._label}) ON (m.tenant_id, m.repo_id)"
        )
        self._listeners: list[Callable[[Scope], None]] = []

    def add_listener(self, listener: Callable[[Scope], None]) -> None:
        """Register a callback for record writes (e.g. invalidating lexical caches)."""
        self._listeners.append(listener)

    def _checked_vector(self, embedding: Vector, context: str) -> list[float]:
        vec = [float(value) for value in embedding]
        if len(vec) != self._dim:
            raise DimensionMismatchError(self._dim, len(vec), context)
        return vec

    def _ensure_encoder_config(self, encoder_id: str) -> None:
        rows = self._run(
            "MERGE (c:ThalamusIndexConfig {hemisphere: $hemisphere}) "
            "ON CREATE SET c.dim = $dim, c.encoder_id = $encoder_id "
            "RETURN c.dim AS dim, c.encoder_id AS encoder_id",
            hemisphere=self._hemisphere.value,
            dim=self._dim,
            encoder_id=encoder_id,
        )
        actual_dim = int(rows[0]["dim"])
        actual_encoder = str(rows[0]["encoder_id"])
        if actual_dim != self._dim or actual_encoder != encoder_id:
            raise StoreError(
                "embedding index configuration mismatch: "
                f"expected {encoder_id}/{self._dim}, found {actual_encoder}/{actual_dim}; rebuild"
            )

    def add(self, record: MemoryRecord, embedding: Vector) -> None:
        vec = self._checked_vector(embedding, "Neo4jStore.add")
        self._run(
            f"MERGE (m:{self._label} "
            "{tenant_id: $tenant_id, repo_id: $repo_id, memory_id: $memory_id}) "
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
        for listener in list(self._listeners):
            listener(record.scope)

    def get(self, ref: MemoryRef) -> MemoryRecord | None:
        rows = self._run(
            f"MATCH (m:{self._label} "
            "{tenant_id: $tenant_id, repo_id: $repo_id, memory_id: $memory_id}) "
            "RETURN m.memory_id AS memory_id, m.hemisphere AS hemisphere, m.kind AS kind, "
            "m.content AS content, m.tenant_id AS tenant_id, m.repo_id AS repo_id, "
            "m.created_at AS created_at, m.metadata_json AS metadata_json",
            memory_id=str(ref.memory_id),
            tenant_id=str(ref.scope.tenant_id),
            repo_id=str(ref.scope.repo_id),
        )
        if not rows:
            return None
        return self._to_record(rows[0])

    def get_many(self, refs: Sequence[MemoryRef]) -> dict[MemoryRef, MemoryRecord]:
        if not refs:
            return {}
        grouped: dict[Scope, list[str]] = {}
        for ref in refs:
            grouped.setdefault(ref.scope, []).append(str(ref.memory_id))

        result: dict[MemoryRef, MemoryRecord] = {}
        for scope, mids in grouped.items():
            rows = self._run(
                f"UNWIND $mids AS mid "
                f"MATCH (m:{self._label} "
                "{tenant_id: $tenant_id, repo_id: $repo_id, memory_id: mid}) "
                "RETURN m.memory_id AS memory_id, m.hemisphere AS hemisphere, m.kind AS kind, "
                "m.content AS content, m.tenant_id AS tenant_id, m.repo_id AS repo_id, "
                "m.created_at AS created_at, m.metadata_json AS metadata_json",
                mids=mids,
                tenant_id=str(scope.tenant_id),
                repo_id=str(scope.repo_id),
            )
            for row in rows:
                rec = self._to_record(row)
                result[rec.ref] = rec
        return result

    def scan(self, scope: Scope) -> list[MemoryRecord]:
        rows = self._run(
            f"MATCH (m:{self._label}) WHERE m.tenant_id = $tenant_id AND m.repo_id = $repo_id "
            "RETURN m.memory_id AS memory_id, m.hemisphere AS hemisphere, m.kind AS kind, "
            "m.content AS content, m.tenant_id AS tenant_id, m.repo_id AS repo_id, "
            "m.created_at AS created_at, m.metadata_json AS metadata_json",
            tenant_id=str(scope.tenant_id),
            repo_id=str(scope.repo_id),
        )
        return [self._to_record(row) for row in rows]

    def scan_with_embeddings(self, scope: Scope) -> list[tuple[MemoryRecord, Vector]]:
        rows = self._run(
            f"MATCH (m:{self._label}) WHERE m.tenant_id = $tenant_id AND m.repo_id = $repo_id "
            "RETURN m.memory_id AS memory_id, m.hemisphere AS hemisphere, m.kind AS kind, "
            "m.content AS content, m.tenant_id AS tenant_id, m.repo_id AS repo_id, "
            "m.created_at AS created_at, m.metadata_json AS metadata_json, "
            "m.embedding AS embedding",
            tenant_id=str(scope.tenant_id),
            repo_id=str(scope.repo_id),
        )
        return [
            (self._to_record(row), [float(value) for value in row["embedding"]])
            for row in rows
        ]

    def search(self, query: Vector, k: int, scope: Scope) -> list[ScoredMemory]:
        vec = self._checked_vector(query, "Neo4jStore.search")
        if k <= 0:
            return []
        fetch_k = max(k * 20, 200)
        # HNSW vector index search via db.index.vector.queryNodes
        try:
            rows = self._run(
                f"CALL db.index.vector.queryNodes('{self._index}', $fetch_k, $vec) "
                "YIELD node AS m, score "
                "WHERE m.tenant_id = $tenant_id AND m.repo_id = $repo_id "
                "RETURN m.memory_id AS memory_id, m.hemisphere AS hemisphere, m.kind AS kind, "
                "m.content AS content, m.tenant_id AS tenant_id, m.repo_id AS repo_id, "
                "m.created_at AS created_at, m.metadata_json AS metadata_json, score "
                "ORDER BY score DESC LIMIT $k",
                fetch_k=fetch_k,
                vec=vec,
                tenant_id=str(scope.tenant_id),
                repo_id=str(scope.repo_id),
                k=k,
            )
        except Exception as exc:
            import logging

            logging.getLogger("thalamus.store").warning(
                "vector index query failed (falling back to linear scan): %s", exc
            )
            rows = []
        if not rows:
            # Fallback scan for un-indexed or newly added nodes
            rows = self._run(
                f"MATCH (m:{self._label}) "
                "WHERE m.tenant_id = $tenant_id AND m.repo_id = $repo_id "
                "WITH m, vector.similarity.cosine(m.embedding, $vec) AS score "
                "RETURN m.memory_id AS memory_id, m.hemisphere AS hemisphere, m.kind AS kind, "
                "m.content AS content, m.tenant_id AS tenant_id, m.repo_id AS repo_id, "
                "m.created_at AS created_at, m.metadata_json AS metadata_json, score "
                "ORDER BY score DESC LIMIT $k",
                vec=vec,
                tenant_id=str(scope.tenant_id),
                repo_id=str(scope.repo_id),
                k=k,
            )
        results: list[ScoredMemory] = []
        for row in rows:
            score = float(row["score"])
            record = self._to_record(row)
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
