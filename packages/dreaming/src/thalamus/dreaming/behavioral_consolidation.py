"""BehavioralConsolidationPass — fold the log WAL's behavioral usage into the brain each tick.

The relevance-credibility sibling of :class:`UsageRefreshPass`, but a step *deeper*: where
``usage-refresh`` recomputes the rung's weights from the raw log files every cycle (the brain reads
its history out of loose JSONL), this pass **consolidates** that history *into* the brain — folding
the newly-observed used-session sets into the durable behavioral store (Track I / Architecture B).
Once the store is durable, the rung reads its weights from the brain and the raw logs become a
disposable write-ahead buffer.

The consolidation seam is injected (the composition root closes over the logs + the store), so
``dreaming`` never imports the store or the logs — it stays pure orchestration, exactly like the
other refresh passes. Deterministic over immutable logs ⇒ it may *act* (§14.3 firewall); the signal
is a behavioral act (a session recalled and used a memory), never the model grading its own prose.
"""

from __future__ import annotations

from collections.abc import Callable

from thalamus.dreaming.base import PassContext, PassKind, PassOutcome


class BehavioralConsolidationPass:
    """Run the injected consolidation seam (read the log slice → fold into the behavioral store)."""

    name = "behavioral-consolidation"
    kind = PassKind.ACTOR

    def __init__(self, consolidate: Callable[[], int]) -> None:
        self._consolidate = consolidate

    def run(self, ctx: PassContext) -> PassOutcome:
        memories = self._consolidate()
        return PassOutcome(
            summary=f"consolidated behavioral usage into the brain: {memories} memory(ies)",
            details={"memories": memories},
        )
