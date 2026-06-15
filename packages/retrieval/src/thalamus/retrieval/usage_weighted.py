"""UsageWeightedRetriever — lift reliably-used memories by RRF-fusing relevance with usage.

The relevance retriever orders candidates by meaning; this rung re-ranks them by **behavioral
usage** — how many distinct sessions each memory has been *recalled-and-used* in (the cross-session
"reliably-useful core"). That property is measured-stable: a 2026-05-31 read found per-memory usage
bimodal (reliably-used vs reliably-ignored) and temporally predictive (early rate predicts late),
so boosting it is defensible now, not speculative.

RRF-fuses the **relevance rank** with a **usage rank**, so a memory the relevance pool already
surfaced but that has repeatedly proven useful rises into the shown slots, while one never used
relies on relevance alone. Because it only re-orders the inner's candidate pool (already bounded to
the relevance frontier), usage can *promote* a used memory but can never summon an irrelevant one.

Firewall (§14.2): the signal is an external/behavioral act — the memory was *used* (``record_usage``
/ footprint attribution) — **never** the model grading the memory's own prose (the Polynoica trap).
Like the hybrid/structural/supersession decorators it composes behind the same ``core.Retriever``
protocol, so the relevance baseline stays pristine and the rung is ablatable; each candidate keeps
its native ``score`` for the off-policy log, and the usage rank/weight are recorded in ``features``.
"""

from __future__ import annotations

from collections.abc import Mapping

from thalamus.core.protocols import Retriever
from thalamus.core.types import Cue, MemoryId, RetrievalResult, ScoredMemory


class UsageWeightedRetriever:
    """Re-rank an inner retriever's candidates by RRF-fusing relevance rank with a usage rank."""

    def __init__(
        self,
        inner: Retriever,
        usage: Mapping[MemoryId, float],
        *,
        rrf_k: int = 60,
        weight: float = 1.0,
    ) -> None:
        # ``usage``: per-memory usage weight (e.g. distinct sessions recalled-and-used). Only the
        # *ordering* matters — RRF ranks it — so raw counts are fine, no normalization. ``weight``
        # tunes how strongly usage re-ranks relative to relevance; ``rrf_k`` (the standard 60) damps
        # deep ranks so a top-of-list memory dominates but a deeper hit still adds a little.
        self._inner = inner
        self._usage = usage
        self._rrf_k = rrf_k
        self._weight = weight

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        candidates = self._inner.retrieve(cue, k).candidates

        # The usage leg: candidates with a positive usage weight, most-used first → a usage rank.
        used = sorted(
            (c for c in candidates if self._usage.get(c.record.memory_id, 0.0) > 0.0),
            key=lambda c: self._usage[c.record.memory_id],
            reverse=True,
        )
        usage_rank = {c.record.memory_id: rank for rank, c in enumerate(used, start=1)}

        fused: list[ScoredMemory] = []
        for rel_rank, candidate in enumerate(candidates, start=1):
            mid = candidate.record.memory_id
            score = 1.0 / (self._rrf_k + rel_rank)  # relevance leg
            features: dict[str, float] = {**candidate.features, "relevance_rank": float(rel_rank)}
            urank = usage_rank.get(mid)
            if urank is not None:  # usage leg, only for memories with a behavioral usage record
                score += self._weight * (1.0 / (self._rrf_k + urank))
                features["usage_rank"] = float(urank)
                features["usage_weight"] = self._usage[mid]
            features["usage_fused"] = score
            # Preserve the native score (the relevance baseline) for honest display + the log.
            fused.append(
                ScoredMemory(record=candidate.record, score=candidate.score, features=features)
            )

        fused.sort(key=lambda scored: scored.features["usage_fused"], reverse=True)
        return RetrievalResult(cue=cue, candidates=fused, shown=fused[: max(k, 0)])
