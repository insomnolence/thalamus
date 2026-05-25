"""L0 baseline retriever — the measuring stick.

``relevance × recency × importance`` with frozen weights — the first
implementation of :class:`thalamus.core.Retriever` and the baseline every gated
rung must beat (deep-dives/outcome-learned-retrieval.md §13.20). Gated rungs
(bandit reweighting, bent geometry) are later implementations behind the *same*
protocol, so swapping them never touches callers.

Depends only on the ``Encoder`` and ``Store`` *protocols* (injected) — never on
their concrete implementations.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime

from thalamus.core.protocols import Encoder, Store
from thalamus.core.types import Cue, RetrievalResult, ScoredMemory


def _utcnow() -> datetime:
    return datetime.now(UTC)


class L0Retriever:
    """Baseline retriever combining relevance, recency, and importance.

    Args:
        encoder: Encodes the cue text when the cue has no precomputed embedding.
        store: Vector store to search (within the cue's scope).
        k_candidates: How many candidates to pull from the store before rescoring.
        w_relevance / w_recency / w_importance: Linear-combination weights.
        recency_halflife_days: Age (days) at which the recency term halves.
        now: Injectable clock (for deterministic tests).
    """

    def __init__(
        self,
        encoder: Encoder,
        store: Store,
        *,
        k_candidates: int = 50,
        w_relevance: float = 1.0,
        w_recency: float = 0.3,
        w_importance: float = 0.3,
        recency_halflife_days: float = 30.0,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._encoder = encoder
        self._store = store
        self._k_candidates = k_candidates
        self._w_relevance = w_relevance
        self._w_recency = w_recency
        self._w_importance = w_importance
        self._recency_halflife_days = recency_halflife_days
        self._now = now

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        if cue.embedding is not None:
            embedding = cue.embedding
        else:
            embedding = self._encoder.encode([cue.text])[0]
        candidates = self._store.search(embedding, self._k_candidates, cue.scope)
        now = self._now()
        rescored = sorted(
            (self._rescore(candidate, now) for candidate in candidates),
            key=lambda item: item.score,
            reverse=True,
        )
        shown = rescored[: max(k, 0)]
        return RetrievalResult(cue=cue, candidates=rescored, shown=shown)

    def _rescore(self, candidate: ScoredMemory, now: datetime) -> ScoredMemory:
        relevance = candidate.features.get("relevance", candidate.score)
        recency = self._recency(candidate.record.created_at, now)
        importance = self._importance(candidate)
        score = (
            self._w_relevance * relevance
            + self._w_recency * recency
            + self._w_importance * importance
        )
        features = {
            **candidate.features,
            "relevance": relevance,
            "recency": recency,
            "importance": importance,
            "score": score,
        }
        return ScoredMemory(record=candidate.record, score=score, features=features)

    def _recency(self, created_at: datetime, now: datetime) -> float:
        age_days = max((now - created_at).total_seconds() / 86_400.0, 0.0)
        return math.exp(-age_days * math.log(2.0) / self._recency_halflife_days)

    @staticmethod
    def _importance(candidate: ScoredMemory) -> float:
        raw = candidate.record.metadata.get("importance", 0.0)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0
