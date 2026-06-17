"""ExploringRetriever — calibrated exploration so the served policy has a *logged propensity* (R-7).

Deterministic top-k serving logs ``propensity = 1.0`` for every shown item. That makes honest
off-policy evaluation (IPS/SNIPS — the per-recall counterfactual the thesis ablation needs)
**undefined, not merely noisy**: a target policy that would show different items had probability 0
under the logging policy, so there is no common support to reweight across. The fix is to serve
*stochastically* with a known, sub-1 inclusion probability, and **log that probability** — the
irreversible-if-deferred half (you can never reconstruct a propensity after the fact).

The scheme is a clean two-policy **mixture** with an EXACT per-item marginal propensity:

- with probability ``1 − ε``  → serve the deterministic top-k;
- with probability ``ε``      → serve a uniform random k-subset of the top-``pool`` candidates.

Marginal probability an item is shown (what IPS needs):
- a top-k item:                ``(1 − ε) + ε·(k/pool)``
- a top-pool-but-not-top-k item: ``ε·(k/pool)``
- anything below top-pool:       ``0``

``ε = 0`` (the default) ⇒ deterministic top-k, propensity 1.0 — identical to today's serving (so
live recall is unchanged until an operator opts in). This rung only *re-selects the
shown subset + records the propensity*; it never changes the candidate ranking (the rungs
above it own relevance). Uniform-within-pool exploration is the boring-but-exact v1; a
relevance-weighted (Plackett-Luce) explorer is a future refinement that trades exactness of the
marginal for better exploration recall.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import replace

from thalamus.core.protocols import Retriever
from thalamus.core.types import Cue, MemoryId, RetrievalResult, ScoredMemory

PROPENSITY_FEATURE = "propensity"


def explore_selection(
    candidates: Sequence[ScoredMemory],
    k: int,
    *,
    epsilon: float,
    pool: int,
    rng: random.Random,
) -> tuple[list[ScoredMemory], dict[MemoryId, float]]:
    """Pure: pick the shown subset under the mixture policy; return (shown, marginal propensities).

    Deterministic (``epsilon <= 0`` or no room to explore) ⇒ top-k with propensity 1.0."""
    n = len(candidates)
    k = min(k, n)
    if k == 0:
        return [], {}
    top_k = list(candidates[:k])
    pool = min(max(pool, k), n)  # explore over the top-`pool`, at least k, at most all candidates
    if epsilon <= 0.0 or pool == k:
        return top_k, {item.record.memory_id: 1.0 for item in top_k}

    top_pool = list(candidates[:pool])
    explore = rng.random() < epsilon
    shown = rng.sample(top_pool, k) if explore else top_k

    explore_marginal = epsilon * (k / pool)
    top_k_ids = {item.record.memory_id for item in top_k}
    propensity = {
        item.record.memory_id: (
            (1.0 - epsilon) + explore_marginal if item.record.memory_id in top_k_ids
            else explore_marginal
        )
        for item in top_pool
    }
    return shown, {item.record.memory_id: propensity[item.record.memory_id] for item in shown}


class ExploringRetriever:
    """Re-select the shown subset stochastically and stamp each shown item's logged propensity.

    Wraps the fully-ranked retriever (outermost rung, just inside ``LoggingRetriever``). The
    realized propensity is written into each shown :class:`ScoredMemory`'s ``features`` under
    ``"propensity"``; ``LoggingRetriever`` reads it there (defaulting to 1.0 when absent)."""

    def __init__(
        self,
        inner: Retriever,
        *,
        epsilon: float,
        pool: int,
        rng: random.Random | None = None,
    ) -> None:
        self._inner = inner
        self._epsilon = epsilon
        self._pool = pool
        self._rng = rng if rng is not None else random.Random()

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        result = self._inner.retrieve(cue, k)
        shown, propensity = explore_selection(
            result.candidates, k, epsilon=self._epsilon, pool=self._pool, rng=self._rng
        )
        shown_with_propensity = [
            replace(
                item,
                features={**item.features, PROPENSITY_FEATURE: propensity[item.record.memory_id]},
            )
            for item in shown
        ]
        return replace(result, shown=shown_with_propensity)
