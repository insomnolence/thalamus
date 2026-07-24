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
/ footprint attribution) — **never** the model grading the memory's own prose (the self-validation
trap). Like the hybrid/structural/supersession decorators it composes behind the same
``core.Retriever`` protocol, so the relevance baseline stays pristine and the rung is ablatable;
each candidate keeps its native ``score`` for the off-policy log, and the usage rank/weight are
recorded in ``features``.
"""

from __future__ import annotations

from collections.abc import Mapping

from thalamus.core.protocols import Retriever
from thalamus.core.types import Cue, MemoryId, MemoryRef, RetrievalResult, ScoredMemory


class UsageWeightsRef:
    """A single-slot mutable holder for the per-memory usage weights — the dreaming refresh seam,
    mirroring the gateway's ``DerivedViewsRef``.

    Usage accrues as the brain is used, so in a long-running serve the weights read once at startup
    go stale. A reader snapshots :attr:`weights` once per retrieval (one atomic attribute read under
    the GIL) and the maintenance thread :meth:`refresh`-es with one atomic assignment, so a
    concurrent refresh is observed whole — never as a torn mix — with no lock."""

    def __init__(self, weights: Mapping[MemoryRef | MemoryId, float] | None = None) -> None:
        self.weights: Mapping[MemoryRef | MemoryId, float] = weights if weights is not None else {}

    def refresh(self, weights: Mapping[MemoryRef | MemoryId, float]) -> None:
        """Atomically replace the current weights (a single ``STORE_ATTR``)."""
        self.weights = weights


class UsageWeightedRetriever:
    """Re-rank an inner retriever's candidates by RRF-fusing relevance rank with a usage rank."""

    def __init__(
        self,
        inner: Retriever,
        weights: UsageWeightsRef,
        *,
        rrf_k: int = 60,
        weight: float = 1.0,
    ) -> None:
        # ``weights``: a refreshable holder of per-memory usage weight (e.g. distinct sessions
        # recalled-and-used). Only the *ordering* matters — RRF ranks it — so raw counts are fine,
        # no normalization. ``weight`` tunes how strongly usage re-ranks relative to relevance;
        # ``rrf_k`` (the standard 60) damps deep ranks so a top-of-list memory dominates but a
        # deeper hit still adds a little.
        self._inner = inner
        self._weights = weights
        self._rrf_k = rrf_k
        self._weight = weight

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        candidates = self._inner.retrieve(cue, k).candidates
        usage = self._weights.weights  # snapshot once (atomic) — a concurrent refresh is whole

        def _get_w(c: ScoredMemory) -> float:
            val = usage.get(c.record.ref)
            if val is not None:
                return val
            return usage.get(c.record.memory_id, 0.0)

        # The usage leg: candidates with a positive usage weight, most-used first → a usage rank.
        used = sorted(
            (c for c in candidates if _get_w(c) > 0.0),
            key=_get_w,
            reverse=True,
        )
        usage_rank = {c.record.ref: rank for rank, c in enumerate(used, start=1)}

        fused: list[ScoredMemory] = []
        for rel_rank, candidate in enumerate(candidates, start=1):
            ref = candidate.record.ref
            base_rel_rank = candidate.features.get("initial_relevance_rank", float(rel_rank))
            # Count relevance once across a composed rung stack. An outer rung adds its
            # independent signal to this score instead of erasing the contribution made here.
            score = candidate.features.get(
                "fusion_score", 1.0 / (self._rrf_k + base_rel_rank)
            )
            features: dict[str, float] = {
                **candidate.features,
                "initial_relevance_rank": base_rel_rank,
                "relevance_rank": float(rel_rank),
            }
            urank = usage_rank.get(ref)
            if urank is not None:  # usage leg, only for memories with a behavioral usage record
                score += self._weight * (1.0 / (self._rrf_k + urank))
                features["usage_rank"] = float(urank)
                features["usage_weight"] = _get_w(candidate)
            features["usage_fused"] = score
            features["fusion_score"] = score
            # Preserve the native score (the relevance baseline) for honest display + the log.
            fused.append(
                ScoredMemory(record=candidate.record, score=candidate.score, features=features)
            )

        fused.sort(key=lambda scored: scored.features["usage_fused"], reverse=True)
        return RetrievalResult(cue=cue, candidates=fused, shown=fused[: max(k, 0)])
