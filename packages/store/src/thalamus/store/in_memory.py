"""In-memory implementation of :class:`thalamus.core.Store`.

Scope-filtered cosine-similarity search over records held in memory. Pure
Python (no torch/numpy) — fine for the small-scale baseline and for tests; the
Neo4j-backed store swaps in behind the same protocol for scale and the shared
graph substrate (deep-dives/foundation.md).

(Referenced from an earlier project of ours: dimension
validation and defensive copies are kept; reimplemented to store a
``MemoryRecord`` + ``Scope`` and return ``ScoredMemory``, and to compute true
cosine rather than assuming unit-normalized inputs.)
"""

from __future__ import annotations

import heapq
import math
import weakref
from collections.abc import Callable, Sequence
from threading import RLock
from types import MethodType
from typing import Any

from thalamus.core.exceptions import DimensionMismatchError
from thalamus.core.types import (
    MemoryRecord,
    MemoryRef,
    Scope,
    ScoredMemory,
    Vector,
)


def _cosine(
    a: tuple[float, ...], a_norm: float, b: tuple[float, ...], b_norm: float
) -> float:
    denom = a_norm * b_norm
    if denom == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    val = dot / denom
    if math.isnan(val):
        return 0.0
    return max(-1.0, min(1.0, val))


class InMemoryStore:
    """In-memory record store with cosine vector search, scoped by tenant/repo.

    Args:
        dim: Dimensionality every stored/queried embedding must have.
    """

    def __init__(self, dim: int) -> None:
        self._dim = dim
        self._lock = RLock()
        self._records: dict[MemoryRef, MemoryRecord] = {}
        self._embeddings: dict[MemoryRef, tuple[float, ...]] = {}
        self._norms: dict[MemoryRef, float] = {}
        self._by_scope: dict[Scope, dict[MemoryRef, None]] = {}
        self._listeners: list[Any] = []

    def add_listener(self, listener: Callable[[Scope], None]) -> None:
        """Register a callback for record writes (e.g. invalidating lexical caches)."""
        # Bound methods must not keep their owner (notably LexicalRetriever) alive forever.
        # Standalone functions, lambdas, and callable objects have no separate owner to retain
        # them, so the registry itself provides their required strong lifetime.
        ref = weakref.WeakMethod(listener) if isinstance(listener, MethodType) else listener
        with self._lock:
            self._listeners.append(ref)

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    @property
    def dim(self) -> int:
        return self._dim

    def add(self, record: MemoryRecord, embedding: Vector) -> None:
        if len(embedding) != self._dim:
            raise DimensionMismatchError(self._dim, len(embedding), "InMemoryStore.add")
        ref = record.ref
        vec = tuple(float(x) for x in embedding)
        norm = math.sqrt(sum(x * x for x in vec))
        with self._lock:
            self._records[ref] = record
            self._embeddings[ref] = vec
            self._norms[ref] = norm
            self._by_scope.setdefault(record.scope, {})[ref] = None
            listeners = list(self._listeners)
        dead: list[Any] = []
        for listener_ref in listeners:
            fn = listener_ref() if isinstance(listener_ref, weakref.WeakMethod) else listener_ref
            if fn is not None:
                fn(record.scope)
            elif isinstance(listener_ref, weakref.WeakMethod):
                dead.append(listener_ref)
        if dead:
            with self._lock:
                self._listeners = [item for item in self._listeners if item not in dead]

    def get(self, ref: MemoryRef) -> MemoryRecord | None:
        with self._lock:
            return self._records.get(ref)

    def get_many(self, refs: Sequence[MemoryRef]) -> dict[MemoryRef, MemoryRecord]:
        with self._lock:
            return {ref: rec for ref in refs if (rec := self._records.get(ref)) is not None}

    def scan(self, scope: Scope) -> list[MemoryRecord]:
        with self._lock:
            refs = list(self._by_scope.get(scope, {}).keys())
            return [
                rec
                for ref in refs
                if (rec := self._records.get(ref)) is not None and rec.scope == scope
            ]

    def scan_with_embeddings(self, scope: Scope) -> list[tuple[MemoryRecord, Vector]]:
        with self._lock:
            refs = list(self._by_scope.get(scope, {}).keys())
            return [
                (rec, self._embeddings[ref])
                for ref in refs
                if (rec := self._records.get(ref)) is not None
                and ref in self._embeddings
                and rec.scope == scope
            ]

    def search(self, query: Vector, k: int, scope: Scope) -> list[ScoredMemory]:
        if len(query) != self._dim:
            raise DimensionMismatchError(self._dim, len(query), "InMemoryStore.search")
        if k <= 0:
            return []
        q_tuple = tuple(float(x) for x in query)
        q_norm = math.sqrt(sum(x * x for x in q_tuple))
        with self._lock:
            refs = list(self._by_scope.get(scope, {}).keys())
            candidates = [
                (record, self._embeddings[ref], self._norms.get(ref, 0.0))
                for ref in refs
                if (record := self._records.get(ref)) is not None
                and record.scope == scope
                and ref in self._embeddings
            ]
        top_candidates = heapq.nlargest(
            k,
            (
                (_cosine(q_tuple, q_norm, embedding, b_norm), idx, record)
                for idx, (record, embedding, b_norm) in enumerate(candidates)
            ),
            key=lambda item: item[0],
        )
        return [
            ScoredMemory(record=record, score=score, features={"relevance": score})
            for score, _idx, record in top_candidates
        ]
