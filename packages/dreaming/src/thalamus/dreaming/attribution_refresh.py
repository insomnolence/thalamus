"""AttributionRefreshPass — keep footprint usage attribution fresh during a long serve.

The deterministic Tier-1 usage signal (which surfaced memories a session's *committed work* drew
on — :class:`~thalamus.structural.FootprintAttributor` over the code graph) is a **re-derivable
view** of the raw recall/commit logs + the current code: it goes stale the moment new recalls or
commits accrue, or the code graph re-derives. Without this pass it was regenerated only by an
offline CLI, so a long serve read a frozen snapshot — silently degrading both the live usage rung
(which consumes the attribution) and the ``verdict``. Each maintenance tick this actor
**recomputes** the signals (from the live graph + logs, via the injected ``recompute`` seam) and
**applies** them (swaps the in-memory holder the rung reads, and rewrites the derived log for the
offline tools, via the injected ``apply`` seam).

Both seams are injected (the composition root closes over the graph + logs), so ``dreaming`` never
imports the attributor or the logs — it stays pure orchestration, exactly like
:class:`UsageRefreshPass`. Deterministic over immutable logs ⇒ it may *act* (§14.3 firewall); the
signal is a behavioral act (the work's footprint overlapping a memory's), never the model grading
its own memory text.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from thalamus.dreaming.base import PassContext, PassKind, PassOutcome


class AttributionRefreshPass[Signal]:
    """Recompute footprint usage attribution from the live graph + logs and swap it in."""

    name = "attribution-refresh"
    kind = PassKind.ACTOR

    def __init__(
        self,
        recompute: Callable[[], Sequence[Signal]],
        apply: Callable[[Sequence[Signal]], None],
    ) -> None:
        self._recompute = recompute
        self._apply = apply

    def run(self, ctx: PassContext) -> PassOutcome:
        signals = self._recompute()
        self._apply(signals)
        return PassOutcome(
            summary=f"refreshed footprint attribution: {len(signals)} signal(s)",
            details={"signals": len(signals)},
        )
