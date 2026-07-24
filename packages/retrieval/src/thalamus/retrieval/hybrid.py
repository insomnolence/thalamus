"""HybridRetriever — fuse semantic (vector) and lexical (BM25) recall by Reciprocal Rank Fusion.

The semantic leg (L0) finds memories by meaning; the lexical leg (BM25) finds them by exact term.
Fusing the two recovers exact-identifier / error-string hits the vector pool misses *without*
losing semantic recall. RRF is the fusion: a memory's fused score is the sum, over the lists it
appears in, of ``1 / (rrf_k + rank)``. RRF needs only *ranks*, not score normalization — so it is
robust to the wildly different scales of cosine and BM25 (the classic weakness of weighted-sum
hybrids), and a memory found by *both* legs is rewarded for it.

Composes two ``core.Retriever``s behind the *same* protocol (like the structural/supersession/
logging decorators), so L0 stays the pristine baseline and hybrid is an ablatable rung. Each fused
candidate keeps its *native* ``score`` (the L0 score, or BM25 for a lexical-only hit) for honest
display + the off-policy logging contract; the fused RRF value drives the ordering and is recorded
in ``features`` alongside each leg's rank.
"""

from __future__ import annotations

from thalamus.core.protocols import Retriever
from thalamus.core.types import Cue, MemoryId, RetrievalResult, ScoredMemory


class HybridRetriever:
    """RRF fusion of a semantic and a lexical :class:`~thalamus.core.Retriever`."""

    def __init__(self, semantic: Retriever, lexical: Retriever, *, rrf_k: int = 60) -> None:
        # rrf_k (the standard 60) damps the contribution of low ranks; a memory near the top of
        # either list dominates, but a deep hit still adds a little.
        self._semantic = semantic
        self._lexical = lexical
        self._rrf_k = rrf_k

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        semantic = self._semantic.retrieve(cue, k).candidates
        lexical = self._lexical.retrieve(cue, k).candidates

        rrf: dict[MemoryId, float] = {}
        semantic_rank: dict[MemoryId, int] = {}
        lexical_rank: dict[MemoryId, int] = {}
        for rank, candidate in enumerate(semantic, start=1):
            mid = candidate.record.memory_id
            rrf[mid] = rrf.get(mid, 0.0) + 1.0 / (self._rrf_k + rank)
            semantic_rank.setdefault(mid, rank)
        for rank, candidate in enumerate(lexical, start=1):
            mid = candidate.record.memory_id
            rrf[mid] = rrf.get(mid, 0.0) + 1.0 / (self._rrf_k + rank)
            lexical_rank.setdefault(mid, rank)

        # Prefer the semantic ScoredMemory (it carries relevance/recency/importance for the log);
        # fall back to the lexical one for a hit the vector pool missed entirely.
        semantic_by_id = {c.record.memory_id: c for c in semantic}
        lexical_by_id = {c.record.memory_id: c for c in lexical}
        fused: list[ScoredMemory] = []
        for mid, rrf_score in rrf.items():
            native = semantic_by_id.get(mid) or lexical_by_id[mid]
            features: dict[str, float] = {**native.features, "rrf": rrf_score}
            if mid in semantic_rank:
                features["semantic_rank"] = float(semantic_rank[mid])
            if mid in lexical_rank:
                features["lexical_rank"] = float(lexical_rank[mid])
                features["lexical"] = lexical_by_id[mid].score
            fused.append(ScoredMemory(record=native.record, score=native.score, features=features))

        fused.sort(key=lambda scored: scored.features["rrf"], reverse=True)
        # Name the final hybrid order as the base relevance rank for any outer RRF rung. Keep the
        # hybrid RRF value separate: promoting it to ``fusion_score`` would change the live policy
        # by carrying both semantic and lexical leg magnitudes into later, independently weighted
        # rungs. ``initial_relevance_rank`` is the preservation contract; ``rrf`` remains telemetry.
        fused = [
            ScoredMemory(
                record=scored.record,
                score=scored.score,
                features={**scored.features, "initial_relevance_rank": float(rank)},
            )
            for rank, scored in enumerate(fused, start=1)
        ]
        return RetrievalResult(cue=cue, candidates=fused, shown=fused[: max(k, 0)])
