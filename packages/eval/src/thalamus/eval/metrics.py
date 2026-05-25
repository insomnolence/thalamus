"""Offline retrieval metrics (the L1 fast proxy, OLR §13.20).

Pure functions over the shown memory ids and the known-relevant set. These are a
*directional proxy*; the truth (L2 task outcomes) and the verdict (L3 brain-on/off
ablation) come later, once outcome capture and an actuator are wired in.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from thalamus.core.types import MemoryId


def recall_at_k(shown: Sequence[MemoryId], relevant: Collection[MemoryId], k: int) -> float:
    if not relevant:
        return 0.0
    top = set(shown[:k])
    return sum(1 for memory_id in relevant if memory_id in top) / len(relevant)


def precision_at_k(shown: Sequence[MemoryId], relevant: Collection[MemoryId], k: int) -> float:
    if k <= 0:
        return 0.0
    relevant_set = set(relevant)
    return sum(1 for memory_id in shown[:k] if memory_id in relevant_set) / k


def reciprocal_rank(shown: Sequence[MemoryId], relevant: Collection[MemoryId]) -> float:
    relevant_set = set(relevant)
    for rank, memory_id in enumerate(shown, start=1):
        if memory_id in relevant_set:
            return 1.0 / rank
    return 0.0


def hit_at_k(shown: Sequence[MemoryId], relevant: Collection[MemoryId], k: int) -> float:
    return 1.0 if set(shown[:k]) & set(relevant) else 0.0
