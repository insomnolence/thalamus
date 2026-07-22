"""Direct structural retrieval — a per-corpus vector index over Brain 2 nodes.

Today a cue reaches Brain 2 only via cross-hemisphere links (from a recalled memory
or a focused file). This adds the *direct* path: embed structural nodes into their own
vector index — **separate from the experiential index** (the no-pollution principle,
deep-dives/foundation.md) — so a cue's text can surface relevant code symbols even when
no memory links them (deep-dives/structural-hemisphere.md, "direct structural retrieval").

The index is a *derived view* over the re-derivable graph (§14.1): rebuilt whenever Brain 2
is re-parsed, never a source of truth. ``InMemoryStructuralIndex`` mirrors ``InMemoryStore``'s
scope-filtered cosine search; a Neo4j-backed index (its own ``vec_structural``) can swap in
behind the same protocol later.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from threading import RLock
from typing import Protocol, runtime_checkable

from thalamus.core.exceptions import DimensionMismatchError
from thalamus.core.protocols import Encoder
from thalamus.core.types import Cue, Scope, StructuralRef, Vector
from thalamus.structural.schema import StructuralNode


def node_text(node: StructuralNode) -> str:
    """The text embedded for a structural node.

    When the node carries embeddable content (``metadata["text"]`` — e.g. a doc
    section's body), embed that, so prose matches on meaning rather than a bare
    heading. Otherwise (code symbols) embed kind + label + the qualified id, which
    gives the encoder the module context, not just a bare symbol name.
    """
    text = node.metadata.get("text")
    if isinstance(text, str) and text.strip():
        return text
    qualified = node.node_id.split(":", 1)[-1]
    if qualified and qualified != node.label:
        return f"{node.kind} {node.label} ({qualified})"
    return f"{node.kind} {node.label}"


@dataclass(frozen=True, slots=True)
class ScoredNode:
    """A structural node with its retrieval score and decision-time features."""

    node: StructuralNode
    score: float
    features: Mapping[str, float] = field(default_factory=dict)


def _cosine(
    a: tuple[float, ...], a_norm: float, b: tuple[float, ...], b_norm: float
) -> float:
    if a_norm == 0.0 or b_norm == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (a_norm * b_norm)


@runtime_checkable
class StructuralIndex(Protocol):
    """A per-corpus vector index over structural nodes (separate from the Store)."""

    def add(self, node: StructuralNode, embedding: Vector) -> None:
        """Index a structural node by its embedding."""
        ...

    def add_many(self, items: Iterable[tuple[StructuralNode, Vector]]) -> None:
        """Index many nodes at once (one batched write for persistent indexes)."""
        ...

    def search(self, query: Vector, k: int, scope: Scope) -> list[ScoredNode]:
        """Return the ``k`` nearest nodes within ``scope`` by vector similarity."""
        ...

    def remove(self, refs: Iterable[StructuralRef]) -> None:
        """Drop the given nodes from the index (for incremental re-embedding)."""
        ...


class InMemoryStructuralIndex:
    """In-memory scope-filtered cosine index over structural nodes.

    Mirrors :class:`thalamus.store.InMemoryStore` — pure Python, fine for the
    re-derived-at-startup Brain 2; the Neo4j-backed index swaps in behind
    :class:`StructuralIndex` for the shared substrate.
    """

    def __init__(self, dim: int) -> None:
        self._dim = dim
        self._lock = RLock()
        self._nodes: dict[StructuralRef, StructuralNode] = {}
        self._embeddings: dict[StructuralRef, tuple[float, ...]] = {}
        self._norms: dict[StructuralRef, float] = {}
        self._by_scope: dict[Scope, dict[StructuralRef, None]] = {}

    def __len__(self) -> int:
        with self._lock:
            return len(self._nodes)

    def _as_checked_vector(self, embedding: Vector, context: str) -> tuple[float, ...]:
        vec = tuple(float(value) for value in embedding)
        if len(vec) != self._dim:
            raise DimensionMismatchError(self._dim, len(vec), context)
        return vec

    def add(self, node: StructuralNode, embedding: Vector) -> None:
        vec = self._as_checked_vector(embedding, "InMemoryStructuralIndex.add")
        norm = math.sqrt(sum(value * value for value in vec))
        with self._lock:
            old_node = self._nodes.get(node.ref)
            if old_node is not None and old_node.scope != node.scope:
                old_map = self._by_scope.get(old_node.scope)
                if old_map is not None:
                    old_map.pop(node.ref, None)
                    if not old_map:
                        self._by_scope.pop(old_node.scope, None)
            self._nodes[node.ref] = node
            self._embeddings[node.ref] = vec
            self._norms[node.ref] = norm
            self._by_scope.setdefault(node.scope, {})[node.ref] = None

    def add_many(self, items: Iterable[tuple[StructuralNode, Vector]]) -> None:
        materialized = [
            (
                node,
                vec := self._as_checked_vector(embedding, "InMemoryStructuralIndex.add_many"),
                math.sqrt(sum(value * value for value in vec)),
            )
            for node, embedding in items
        ]
        with self._lock:
            for node, vec, norm in materialized:
                old_node = self._nodes.get(node.ref)
                if old_node is not None and old_node.scope != node.scope:
                    old_map = self._by_scope.get(old_node.scope)
                    if old_map is not None:
                        old_map.pop(node.ref, None)
                        if not old_map:
                            self._by_scope.pop(old_node.scope, None)
                self._nodes[node.ref] = node
                self._embeddings[node.ref] = vec
                self._norms[node.ref] = norm
                self._by_scope.setdefault(node.scope, {})[node.ref] = None

    def remove(self, refs: Iterable[StructuralRef]) -> None:
        materialized_refs = list(refs)
        with self._lock:
            for ref in materialized_refs:
                old_node = self._nodes.pop(ref, None)
                if old_node is not None:
                    old_map = self._by_scope.get(old_node.scope)
                    if old_map is not None:
                        old_map.pop(ref, None)
                        if not old_map:
                            self._by_scope.pop(old_node.scope, None)
                self._embeddings.pop(ref, None)
                self._norms.pop(ref, None)

    def search(self, query: Vector, k: int, scope: Scope) -> list[ScoredNode]:
        if k <= 0:
            return []
        q = self._as_checked_vector(query, "InMemoryStructuralIndex.search")
        q_norm = math.sqrt(sum(value * value for value in q))
        with self._lock:
            refs = list(self._by_scope.get(scope, {}).keys())
            candidates = [
                (node, self._embeddings[ref], self._norms.get(ref, 0.0))
                for ref in refs
                if (node := self._nodes.get(ref)) is not None
                and node.scope == scope
                and ref in self._embeddings
            ]
        scored: list[ScoredNode] = []
        for node, embedding, b_norm in candidates:
            score = _cosine(q, q_norm, embedding, b_norm)
            scored.append(ScoredNode(node=node, score=score, features={"relevance": score}))
        return heapq.nlargest(k, scored, key=lambda item: item.score)


class StructuralRetriever:
    """Direct structural retrieval: encode the cue, search the structural index.

    The Brain-2 counterpart to the experiential ``Retriever`` — a separate seam because
    it returns structural nodes, not memory records. Injected (optionally) into the
    Gateway, which fuses its hits into the payload's related-code section.

    Each retriever covers one *corpus* over its own index (the no-pollution principle):
    ``corpus`` (e.g. ``"code"`` / ``"docs"``) tags its hits so the gateway can group them
    into separate payload sections and keep their vector spaces from muddying each other.
    """

    def __init__(self, encoder: Encoder, index: StructuralIndex, *, corpus: str = "code") -> None:
        self._encoder = encoder
        self._index = index
        self.corpus = corpus

    def retrieve(self, cue: Cue, k: int) -> list[ScoredNode]:
        embedding = (
            cue.embedding if cue.embedding is not None else self._encoder.encode([cue.text])[0]
        )
        return self._index.search(embedding, k, cue.scope)
