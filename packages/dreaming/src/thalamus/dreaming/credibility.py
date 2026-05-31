"""CredibilityPass — the fate-based credibility actor (OLR §13.17; dreaming.md "Pass: fate-based
credibility").

The dreaming home of per-memory credibility: each cycle, read the curated memories and assess each
one's fate-based standing (superseded / reverted / reused). Deterministic and re-derivable over the
immutable logs (safe to re-run), so it may *act* (§14.3 firewall).

To keep ``dreaming`` decoupled from the fate primitives (which live in ``experiential``), the
assessment is an **injected** callable the composition root supplies — this pass is pure
orchestration over the durable Brain-1 store + that assessor. v0 records the credibility
distribution + the negatives to the dream log (an observable, regenerable derived view); a durable
per-memory credibility store and its consumers (retrieval down-weighting, belief reconciliation) are
the documented follow-ups — this pass is the producer they will read.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence

from thalamus.core.types import MemoryId, MemoryRecord
from thalamus.dreaming.base import PassContext, PassKind, PassOutcome

# Curated memories -> {memory_id: (polarity, tier)} as plain strings, so ``dreaming`` need not
# depend on the fate types in ``experiential``; the composition root closes over ``compute_fate``
# and the fate context (supersession index + recall/usage logs + git reverts).
CredibilityAssessor = Callable[[Sequence[MemoryRecord]], Mapping[MemoryId, tuple[str, str]]]


class CredibilityPass:
    """Assess each curated memory's fate-based credibility and record the distribution."""

    name = "credibility"
    kind = PassKind.ACTOR

    def __init__(self, assess: CredibilityAssessor) -> None:
        self._assess = assess

    def run(self, ctx: PassContext) -> PassOutcome:
        if ctx.store is None:
            return PassOutcome.skipped("no store handle wired")
        memories = [
            record
            for record in ctx.store.scan(ctx.scope)
            if not record.memory_id.startswith("episode:")  # credibility is for the belief layer
        ]
        verdicts = self._assess(memories)
        counts: Counter[str] = Counter(polarity for polarity, _tier in verdicts.values())
        negatives = sorted(
            str(memory_id)
            for memory_id, (polarity, _tier) in verdicts.items()
            if polarity == "negative"
        )
        return PassOutcome(
            summary=(
                f"credibility over {len(verdicts)} curated memory(ies): "
                f"{counts.get('positive', 0)} positive / {counts.get('negative', 0)} negative / "
                f"{counts.get('unknown', 0)} unknown"
            ),
            details={"polarity": dict(counts), "negatives": negatives},
        )
