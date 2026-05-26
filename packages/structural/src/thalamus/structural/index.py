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

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from thalamus.core.exceptions import DimensionMismatchError
from thalamus.core.protocols import Encoder
from thalamus.core.types import Cue, Scope, StructuralRef, Vector
from thalamus.structural.schema import StructuralNode


def node_text(node: StructuralNode) -> str:
    """The text embedded for a structural node: kind, label, and qualified path.

    The qualified id (e.g. ``function:pkg.mod.func`` -> ``pkg.mod.func``) gives the
    encoder the module context, not just a bare symbol name.
    """
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


def _cosine(a: tuple[float, ...], a_norm: float, b: tuple[float, ...]) -> float:
    b_norm = math.sqrt(sum(value * value for value in b))
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

    def search(self, query: Vector, k: int, scope: Scope) -> list[ScoredNode]:
        """Return the ``k`` nearest nodes within ``scope`` by vector similarity."""
        ...


class InMemoryStructuralIndex:
    """In-memory scope-filtered cosine index over structural nodes.

    Mirrors :class:`thalamus.store.InMemoryStore` — pure Python, fine for the
    re-derived-at-startup Brain 2; the Neo4j-backed index swaps in behind
    :class:`StructuralIndex` for the shared substrate.
    """

    def __init__(self, dim: int) -> None:
        self._dim = dim
        self._nodes: dict[StructuralRef, StructuralNode] = {}
        self._embeddings: dict[StructuralRef, tuple[float, ...]] = {}

    def __len__(self) -> int:
        return len(self._nodes)

    def _as_checked_vector(self, embedding: Vector, context: str) -> tuple[float, ...]:
        vec = tuple(float(value) for value in embedding)
        if len(vec) != self._dim:
            raise DimensionMismatchError(self._dim, len(vec), context)
        return vec

    def add(self, node: StructuralNode, embedding: Vector) -> None:
        vec = self._as_checked_vector(embedding, "InMemoryStructuralIndex.add")
        self._nodes[node.ref] = node
        self._embeddings[node.ref] = vec

    def search(self, query: Vector, k: int, scope: Scope) -> list[ScoredNode]:
        q = self._as_checked_vector(query, "InMemoryStructuralIndex.search")
        q_norm = math.sqrt(sum(value * value for value in q))
        scored: list[ScoredNode] = []
        for ref, node in self._nodes.items():
            if node.scope != scope:
                continue
            score = _cosine(q, q_norm, self._embeddings[ref])
            scored.append(ScoredNode(node=node, score=score, features={"relevance": score}))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[: max(k, 0)]


class StructuralRetriever:
    """Direct structural retrieval: encode the cue, search the structural index.

    The Brain-2 counterpart to the experiential ``Retriever`` — a separate seam because
    it returns structural nodes, not memory records. Injected (optionally) into the
    Gateway, which fuses its hits into the payload's related-code section.
    """

    def __init__(self, encoder: Encoder, index: StructuralIndex) -> None:
        self._encoder = encoder
        self._index = index

    def retrieve(self, cue: Cue, k: int) -> list[ScoredNode]:
        embedding = (
            cue.embedding if cue.embedding is not None else self._encoder.encode([cue.text])[0]
        )
        return self._index.search(embedding, k, cue.scope)
