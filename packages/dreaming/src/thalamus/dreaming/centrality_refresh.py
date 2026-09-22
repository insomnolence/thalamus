"""CentralityRefreshPass — keep the structural-centrality recall rung fresh during a long serve.

The structural sibling of :class:`~thalamus.dreaming.usage_refresh.UsageRefreshPass`. The centrality
weights that drive the ``StructuralCentralityRetriever`` (how connected each memory is to Brain 2 —
the summed degree of the code nodes it cross-links to) are read once at composition; in a
long-running serve they go stale as Brain 2 + the cross-links re-derive (new/changed code, new
links). Each maintenance tick this actor **recomputes** the weights from the current graph + links
and **swaps** them into the rung's holder through the injected ``refresh`` seam — so a memory's
"well-connected to the code graph" standing tracks the live structure, not the restart snapshot.

The memory set is the cycle's **live** Brain 1 (:meth:`PassContext.memories`), not a snapshot
taken when the serve started. It used to close over the composition-time ``store.scan``, so a
memory written mid-serve was absent from the weights and its centrality rung read 0 until the
next restart — silently, since an absent memory is indistinguishable from an unlinked one. Reusing
the cycle's shared scan costs nothing extra, and the gate over this pass already keys on Brain 1.

It is scheduled *after* the structural re-derive + link-resolution passes (which rebuild the graph
and re-link episodes), so it reads the freshly-derived topology. Both seams are injected (the
composition root closes over the graph + links for ``recompute`` and over
``CentralityWeightsRef.refresh``), so ``dreaming`` never imports the retrieval rung or the
structural package — it stays pure orchestration. Deterministic over the current graph ⇒ it may
*act* (§14.3 firewall); the signal is graph topology, never the model grading its own memory text.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from thalamus.core.types import MemoryRef
from thalamus.dreaming.base import PassContext, PassKind, PassOutcome


class CentralityRefreshPass:
    """Recompute per-memory structural-centrality weights from the live graph + cross-links."""

    name = "centrality-refresh"
    kind = PassKind.ACTOR

    def __init__(
        self,
        recompute: Callable[[Sequence[MemoryRef]], Mapping[MemoryRef, float]],
        refresh: Callable[[Mapping[MemoryRef, float]], None],
    ) -> None:
        self._recompute = recompute
        self._refresh = refresh

    def run(self, ctx: PassContext) -> PassOutcome:
        if ctx.store is None:
            # Without Brain 1 the memory set would be empty, and publishing empty weights would
            # wipe the rung rather than leave it alone. Skipping is the safe reading.
            return PassOutcome.skipped("no store handle wired")
        weights = self._recompute([record.ref for record in ctx.memories()])
        self._refresh(weights)
        return PassOutcome(
            summary=f"refreshed centrality weights: {len(weights)} memory(ies) linked to code",
            details={"weighted": len(weights)},
        )
