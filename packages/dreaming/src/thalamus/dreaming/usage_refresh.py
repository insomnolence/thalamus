"""UsageRefreshPass — keep the usage-weighted recall rung fresh during a long serve.

The relevance-credibility sibling of :class:`LinkResolutionPass`. The usage weights that drive the
``UsageWeightedRetriever`` (which memories have been recalled-and-used across sessions) are read
once at composition; in a long-running serve they go stale as new ``record_usage`` signals accrue.
Each maintenance tick this actor **recomputes** the weights from the durable logs and **swaps** them
into the rung's holder through the injected ``refresh`` seam — so "more use → the right memories
rise" happens live, not only at restart.

Both seams are injected (the composition root closes over the log paths for ``recompute`` and over
``UsageWeightsRef.refresh``), so ``dreaming`` never imports the retrieval rung or the logs — it
stays pure orchestration. Deterministic over immutable logs ⇒ it may *act* (§14.3 firewall); the
signal is a behavioral act (usage), never the model grading its own memory text.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from thalamus.core.types import MemoryId, MemoryRef
from thalamus.dreaming.base import PassContext, PassKind, PassOutcome


class UsageRefreshPass:
    """Recompute per-memory usage weights from the durable logs and swap them into the rung."""

    name = "usage-refresh"
    kind = PassKind.ACTOR

    def __init__(
        self,
        recompute: Callable[[], Mapping[MemoryRef | MemoryId, float]],
        refresh: Callable[[Mapping[MemoryRef | MemoryId, float]], None],
    ) -> None:
        self._recompute = recompute
        self._refresh = refresh

    def run(self, ctx: PassContext) -> PassOutcome:
        weights = self._recompute()
        self._refresh(weights)
        return PassOutcome(
            summary=f"refreshed usage weights: {len(weights)} memory(ies) with a usage signal",
            details={"weighted": len(weights)},
        )
