"""Neo4j-backed per-corpus structural index — persist Brain-2 embeddings (scale, §14.1).

The persisted counterpart to :class:`InMemoryStructuralIndex`. Today the per-corpus vector
index is rebuilt in memory on every serve start — embedding every node — which is the
dominant Brain-2 start cost at scale. This persists each structural node's embedding (on its
``SNode``, alongside the graph) + a ``corpus`` tag, so a restart with unchanged source reuses
the stored vectors instead of re-encoding (the win materializes once paired with a persisted
graph + content-hashed incremental ingestion).

Mirrors :class:`thalamus.store.Neo4jStore`: a cosine vector index + an encoder/dim
compatibility check (the fixed-dim-at-creation gotcha fails loudly rather than corrupting).
Search is **scope- and corpus-filtered** cosine — the corpus filter keeps code and docs in
separate vector spaces (the no-pollution principle), exactly as the per-corpus in-memory
indexes do. A derived view (§14.1): rebuilt from source on ``--rebuild``, never a source of
truth. Requires the ``neo4j`` extra (driver injected, matching the graph + store).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from thalamus.core.exceptions import DimensionMismatchError, StoreError
from thalamus.core.types import Scope, StructuralRef, Vector
from thalamus.structural.index import ScoredNode
from thalamus.structural.neo4j_graph import _NODE, _node_props, _run, _to_node
from thalamus.structural.schema import StructuralNode

if TYPE_CHECKING:
    from neo4j import Driver

_VECTOR_INDEX = "vec_struct"  # one cosine index over SNode.embedding; corpus is a filter


class Neo4jStructuralIndex:
    """``StructuralIndex`` over Neo4j: persisted per-corpus node embeddings + cosine search."""

    def __init__(
        self,
        driver: Driver,
        scope: Scope,
        *,
        dim: int,
        corpus: str = "code",
        database: str = "neo4j",
        encoder_id: str | None = None,
    ) -> None:
        self._driver = driver
        self._scope = scope
        self._dim = dim
        self._corpus = corpus
        self._database = database
        self._ensure_index()
        if encoder_id is not None:
            self._ensure_encoder_config(encoder_id)

    def _ensure_index(self) -> None:
        # dim/label are trusted internal values; all content flows through parameters.
        _run(
            self._driver,
            self._database,
            f"CREATE VECTOR INDEX {_VECTOR_INDEX} IF NOT EXISTS "
            f"FOR (m:{_NODE}) ON (m.embedding) "
            f"OPTIONS {{indexConfig: {{`vector.dimensions`: {self._dim}, "
            f"`vector.similarity_function`: 'cosine'}}}}",
        )

    def _ensure_encoder_config(self, encoder_id: str) -> None:
        rows = _run(
            self._driver,
            self._database,
            "MERGE (c:ThalamusStructConfig {corpus: $corpus}) "
            "ON CREATE SET c.dim = $dim, c.encoder_id = $encoder_id "
            "RETURN c.dim AS dim, c.encoder_id AS encoder_id",
            corpus=self._corpus,
            dim=self._dim,
            encoder_id=encoder_id,
        )
        if int(rows[0]["dim"]) != self._dim or str(rows[0]["encoder_id"]) != encoder_id:
            raise StoreError(
                "structural index config mismatch: "
                f"expected {encoder_id}/{self._dim}, found "
                f"{rows[0]['encoder_id']}/{rows[0]['dim']}; rebuild the corpus"
            )

    def _checked_vector(self, embedding: Vector, context: str) -> list[float]:
        vec = [float(value) for value in embedding]
        if len(vec) != self._dim:
            raise DimensionMismatchError(self._dim, len(vec), context)
        return vec

    def add(self, node: StructuralNode, embedding: Vector) -> None:
        self.add_many([(node, embedding)])

    def add_many(self, items: Iterable[tuple[StructuralNode, Vector]]) -> None:
        # One UNWIND for all nodes — O(1) round-trips, not O(nodes) (the cold-build cost fix).
        rows = [
            {
                **_node_props(node),
                "embedding": self._checked_vector(embedding, "Neo4jStructuralIndex.add_many"),
                "corpus": self._corpus,
            }
            for node, embedding in items
        ]
        if not rows:
            return
        # MERGE each SNode (the graph may already have written it) and set its embedding + corpus.
        _run(
            self._driver,
            self._database,
            f"UNWIND $rows AS r MERGE (m:{_NODE} "
            "{tenant_id: r.tenant_id, repo_id: r.repo_id, node_id: r.node_id}) "
            "SET m.kind = r.kind, m.label = r.label, m.anchor_path = r.anchor_path, "
            "m.anchor_line_start = r.anchor_line_start, m.anchor_line_end = r.anchor_line_end, "
            "m.metadata_json = r.metadata_json, m.embedding = r.embedding, m.corpus = r.corpus",
            rows=rows,
        )

    def remove(self, refs: Iterable[StructuralRef]) -> None:
        # Clear only the index properties; the graph owns node deletion (which, being a
        # DETACH DELETE, already drops the embedding — so this no-ops on already-gone nodes).
        ids = [ref.node_id for ref in refs if ref.scope == self._scope]
        if not ids:
            return
        _run(
            self._driver,
            self._database,
            f"MATCH (m:{_NODE}) WHERE m.tenant_id = $tenant_id AND m.repo_id = $repo_id "
            "AND m.node_id IN $ids REMOVE m.embedding, m.corpus",
            tenant_id=str(self._scope.tenant_id),
            repo_id=str(self._scope.repo_id),
            ids=ids,
        )

    def search(self, query: Vector, k: int, scope: Scope) -> list[ScoredNode]:
        vec = self._checked_vector(query, "Neo4jStructuralIndex.search")
        if k <= 0 or scope != self._scope:
            return []
        # Scope + corpus filter before ranking (no-pollution + isolation), then cosine.
        rows = _run(
            self._driver,
            self._database,
            f"MATCH (m:{_NODE}) "
            "WHERE m.tenant_id = $tenant_id AND m.repo_id = $repo_id "
            "AND m.corpus = $corpus AND m.embedding IS NOT NULL "
            "WITH m, vector.similarity.cosine(m.embedding, $vec) AS score "
            "RETURN m, score ORDER BY score DESC LIMIT $k",
            vec=vec,
            tenant_id=str(scope.tenant_id),
            repo_id=str(scope.repo_id),
            corpus=self._corpus,
            k=k,
        )
        return [
            ScoredNode(
                node=_to_node(dict(row["m"])),
                score=float(row["score"]),
                features={"relevance": float(row["score"])},
            )
            for row in rows
        ]
