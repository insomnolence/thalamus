"""StructuralCentralityRetriever — lift well-connected memories by RRF-fusing relevance with
the memory's *structural centrality* in Brain 2 (L-R2, the global / query-independent leg).

Where :class:`thalamus.retrieval.usage_weighted.UsageWeightedRetriever` lifts a memory by its
**behavioral** standing (distinct sessions it was recalled-and-used in), this lifts a memory by its
**structural** standing — how connected it is to the code graph. A memory that cross-links to many
code nodes, and especially to *central* (high-degree) ones, is well-anchored to Brain-2 knowledge:
it is "about" a load-bearing part of the system, so when it is relevant at all it tends to be the
more consequential context. That is the "well-connected to Brain-2" relevance-credibility signal
(ROADMAP L-R2 / §13.19), made a global per-memory weight.

This is the **global** complement to the query-local ``StructuralRelevanceRetriever`` (committed
f5153a6): that rung boosts memories cross-linked to the nodes the *current cue* is about (relevance
of code to the query); this one boosts memories by their standing in the graph regardless of cue
(the memory's connectedness), exactly as L-R1 weights by usage regardless of cue. The two compose
behind the same ``core.Retriever`` seam — query-local relevance then global centrality.

RRF-fuses the **relevance rank** with a **centrality rank**, so a memory the relevance pool already
surfaced but that is well-connected to Brain-2 rises into the shown slots, while one with no links
relies on relevance alone. Because it only re-orders the inner's candidate pool (already bounded to
the relevance frontier), centrality can *promote* a connected memory but can never summon an
irrelevant one.

Firewall (§14.2 / §14.3, hard): the weight is computed **only** from the cross-link/graph topology
(``CrossLinkIndex`` memory→nodes + ``StructuralGraph`` node degree) — a deterministic, external fact
of how the brain's hemispheres are wired — and **never** from the memory's own text/embedding or any
model judgment of its prose (the Polynoica self-reference trap). Each candidate keeps its native
``score`` for the off-policy log, and the centrality rank/weight are recorded in ``features``. The
weight is ablatable (``weight=0.0`` turns the layer off) and the rung is removable behind the seam.
"""

from __future__ import annotations

from collections.abc import Mapping

from thalamus.core.protocols import Retriever
from thalamus.core.types import Cue, MemoryRef, RetrievalResult, ScoredMemory


class CentralityWeightsRef:
    """A single-slot mutable holder for the per-memory structural-centrality weights — the dreaming
    refresh seam, mirroring :class:`~thalamus.retrieval.usage_weighted.UsageWeightsRef`.

    Brain 2 (the code graph) and the cross-links are re-derived live in a long-running serve (new
    code, new links), so centrality computed once at startup goes stale. A reader snapshots
    :attr:`weights` once per retrieval (one atomic attribute read under the GIL) and the maintenance
    thread :meth:`refresh`-es with one atomic assignment, so a concurrent refresh is observed
    whole — never as a torn mix — with no lock."""

    def __init__(self, weights: Mapping[MemoryRef, float] | None = None) -> None:
        self.weights: Mapping[MemoryRef, float] = weights if weights is not None else {}

    def refresh(self, weights: Mapping[MemoryRef, float]) -> None:
        """Atomically replace the current weights (a single ``STORE_ATTR``)."""
        self.weights = weights


class StructuralCentralityRetriever:
    """Re-rank an inner retriever's candidates: RRF-fuse relevance rank with a centrality rank."""

    def __init__(
        self,
        inner: Retriever,
        weights: CentralityWeightsRef,
        *,
        rrf_k: int = 60,
        weight: float = 1.0,
    ) -> None:
        # ``weights``: a refreshable holder of per-memory centrality weight (e.g. summed degree of
        # the code nodes the memory cross-links to). Only the *ordering* matters — RRF ranks it — so
        # the raw magnitude is fine, no normalization. ``weight`` tunes how strongly centrality
        # re-ranks relative to relevance (0.0 ablates the layer); ``rrf_k`` (the standard 60) damps
        # deep ranks so a top-of-list memory dominates but a deeper hit still adds a little.
        self._inner = inner
        self._weights = weights
        self._rrf_k = rrf_k
        self._weight = weight

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        candidates = self._inner.retrieve(cue, k).candidates
        centrality = self._weights.weights  # snapshot once (atomic) — a concurrent refresh is whole

        # The centrality leg: candidates with a positive centrality weight, most-central first → a
        # centrality rank. Keyed by MemoryRef (scope + id), as the cross-links are.
        central = sorted(
            (c for c in candidates if centrality.get(c.record.ref, 0.0) > 0.0),
            key=lambda c: centrality[c.record.ref],
            reverse=True,
        )
        centrality_rank = {c.record.ref: rank for rank, c in enumerate(central, start=1)}

        fused: list[ScoredMemory] = []
        for rel_rank, candidate in enumerate(candidates, start=1):
            ref = candidate.record.ref
            score = 1.0 / (self._rrf_k + rel_rank)  # relevance leg
            features: dict[str, float] = {**candidate.features, "relevance_rank": float(rel_rank)}
            crank = centrality_rank.get(ref)
            if crank is not None:  # centrality leg, only for memories with a cross-link footprint
                score += self._weight * (1.0 / (self._rrf_k + crank))
                features["centrality_rank"] = float(crank)
                features["centrality_weight"] = centrality[ref]
            features["centrality_fused"] = score
            # Preserve the native score (the relevance baseline) for honest display + the log.
            fused.append(
                ScoredMemory(record=candidate.record, score=candidate.score, features=features)
            )

        fused.sort(key=lambda scored: scored.features["centrality_fused"], reverse=True)
        return RetrievalResult(cue=cue, candidates=fused, shown=fused[: max(k, 0)])
