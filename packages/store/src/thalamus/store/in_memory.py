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
from thalamus.core.types import MemoryId, MemoryRecord, Scope, ScoredMemory, Vector


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
        self._records: dict[MemoryId, MemoryRecord] = {}
        self._embeddings: dict[MemoryId, tuple[float, ...]] = {}

    def __len__(self) -> int:
        return len(self._records)

    def _as_checked_vector(self, embedding: Vector, context: str) -> tuple[float, ...]:
        vec = tuple(float(value) for value in embedding)
        if len(vec) != self._dim:
            raise DimensionMismatchError(self._dim, len(vec), context)
        return vec

    def add(self, record: MemoryRecord, embedding: Vector) -> None:
        vec = self._as_checked_vector(embedding, "InMemoryStore.add")
        self._records[record.memory_id] = record
        self._embeddings[record.memory_id] = vec

    def get(self, memory_id: MemoryId) -> MemoryRecord | None:
        return self._records.get(memory_id)

    def search(self, query: Vector, k: int, scope: Scope) -> list[ScoredMemory]:
        q = self._as_checked_vector(query, "InMemoryStore.search")
        q_norm = math.sqrt(sum(value * value for value in q))
        scored: list[ScoredMemory] = []
        for memory_id, record in self._records.items():
            if record.scope != scope:
                continue
            score = _cosine(q, q_norm, self._embeddings[memory_id])
            scored.append(ScoredMemory(record=record, score=score, features={"relevance": score}))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[: max(k, 0)]
