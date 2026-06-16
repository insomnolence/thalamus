"""CentralityRefreshPass — keep the structural-centrality recall rung fresh during a long serve.

The structural sibling of :class:`~thalamus.dreaming.usage_refresh.UsageRefreshPass`. The centrality
weights that drive the ``StructuralCentralityRetriever`` (how connected each memory is to Brain 2 —
the summed degree of the code nodes it cross-links to) are read once at composition; in a
long-running serve they go stale as Brain 2 + the cross-links re-derive (new/changed code, new
links). Each maintenance tick this actor **recomputes** the weights from the current graph + links
and **swaps** them into the rung's holder through the injected ``refresh`` seam — so a memory's
"well-connected to the code graph" standing tracks the live structure, not the restart snapshot.

It is scheduled *after* the structural re-derive + link-resolution passes (which rebuild the graph
and re-link episodes), so it reads the freshly-derived topology. Both seams are injected (the
composition root closes over the graph + links for ``recompute`` and over
``CentralityWeightsRef.refresh``), so ``dreaming`` never imports the retrieval rung or the
structural package — it stays pure orchestration. Deterministic over the current graph ⇒ it may
*act* (§14.3 firewall); the signal is graph topology, never the model grading its own memory text.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from thalamus.core.types import MemoryRef
from thalamus.dreaming.base import PassContext, PassKind, PassOutcome


class CentralityRefreshPass:
    """Recompute per-memory structural-centrality weights from the live graph + cross-links."""

    name = "centrality-refresh"
    kind = PassKind.ACTOR

    def __init__(
        self,
        recompute: Callable[[], Mapping[MemoryRef, float]],
        refresh: Callable[[Mapping[MemoryRef, float]], None],
    ) -> None:
        self._recompute = recompute
        self._refresh = refresh

    def run(self, ctx: PassContext) -> PassOutcome:
        weights = self._recompute()
        self._refresh(weights)
        return PassOutcome(
            summary=f"refreshed centrality weights: {len(weights)} memory(ies) linked to code",
            details={"weighted": len(weights)},
        )
