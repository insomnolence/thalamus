"""In-memory implementation of :class:`thalamus.core.Store`.

Scope-filtered cosine-similarity search over records held in memory. Pure
Python (no torch/numpy) — fine for the small-scale baseline and for tests; the
Neo4j-backed store swaps in behind the same protocol for scale and the shared
graph substrate (deep-dives/foundation.md).

(Referenced from Polynoica's ``memory/store/in_memory.py``: dimension
validation and defensive copies are kept; reimplemented to store a
``MemoryRecord`` + ``Scope`` and return ``ScoredMemory``, and to compute true
cosine rather than assuming unit-normalized inputs.)
"""

from __future__ import annotations

import math

from thalamus.core.exceptions import DimensionMismatchError
from thalamus.core.types import MemoryRecord, MemoryRef, Scope, ScoredMemory, Vector


def _cosine(a: tuple[float, ...], a_norm: float, b: tuple[float, ...]) -> float:
    b_norm = math.sqrt(sum(value * value for value in b))
    if a_norm == 0.0 or b_norm == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (a_norm * b_norm)


class InMemoryStore:
    """In-memory record store with cosine vector search, scoped by tenant/repo.

    Args:
        dim: Dimensionality every stored/queried embedding must have.
    """

    def __init__(self, dim: int) -> None:
        self._dim = dim
        self._records: dict[MemoryRef, MemoryRecord] = {}
        self._embeddings: dict[MemoryRef, tuple[float, ...]] = {}

    def __len__(self) -> int:
        return len(self._records)

    def _as_checked_vector(self, embedding: Vector, context: str) -> tuple[float, ...]:
        vec = tuple(float(value) for value in embedding)
        if len(vec) != self._dim:
            raise DimensionMismatchError(self._dim, len(vec), context)
        return vec

    def add(self, record: MemoryRecord, embedding: Vector) -> None:
        vec = self._as_checked_vector(embedding, "InMemoryStore.add")
        self._records[record.ref] = record
        self._embeddings[record.ref] = vec

    def get(self, ref: MemoryRef) -> MemoryRecord | None:
        return self._records.get(ref)

    def scan(self, scope: Scope) -> list[MemoryRecord]:
        return [record for record in self._records.values() if record.scope == scope]

    def scan_with_embeddings(self, scope: Scope) -> list[tuple[MemoryRecord, Vector]]:
        return [
            (record, self._embeddings[ref])
            for ref, record in self._records.items()
            if record.scope == scope
        ]

    def search(self, query: Vector, k: int, scope: Scope) -> list[ScoredMemory]:
        q = self._as_checked_vector(query, "InMemoryStore.search")
        q_norm = math.sqrt(sum(value * value for value in q))
        scored: list[ScoredMemory] = []
        for ref, record in self._records.items():
            if record.scope != scope:
                continue
            score = _cosine(q, q_norm, self._embeddings[ref])
            scored.append(ScoredMemory(record=record, score=score, features={"relevance": score}))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[: max(k, 0)]
