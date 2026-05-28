"""BeliefAuditPass — the propose-only belief-obsolescence auditor (§13.18-D2).

A *proposer* (§14.3 firewall): it may suggest, never act. Where the staleness
warning fires on *any* missing footprint file (a file moved), this proposes
actually *superseding* a belief only when its footprint has wholly vanished —
every file it claims is gone from disk, so the code the belief is entirely about
no longer exists and the belief is a strong obsolescence candidate. It NEVER
supersedes (§14.4: heavy refactors throw false positives; time + outcomes
arbitrate). Proposals ride out in the pass report, which the scheduler records to
the dream log — a durable, auditable suggestion, not an authority.

v0 scope: file-level only, proposals to the dream log. Symbol-level resolution
against Brain 2, a durable proposal index, and surfacing proposals on recall are
the documented extensions behind this same proposer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thalamus.dreaming._curated import curated_footprints
from thalamus.dreaming.base import PassContext, PassKind, PassOutcome


@dataclass(frozen=True, slots=True)
class SupersessionProposal:
    """A propose-only suggestion that a belief be retired, with its evidence."""

    memory_id: str
    reason: str
    evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"memory_id": self.memory_id, "reason": self.reason, "evidence": list(self.evidence)}


class BeliefAuditPass:
    """Propose superseding curated beliefs whose code footprint has wholly vanished."""

    name = "belief-audit"
    kind = PassKind.PROPOSER

    def run(self, ctx: PassContext) -> PassOutcome:
        if ctx.store is None or ctx.repo_root is None:
            return PassOutcome.skipped("no store/repo_root handle wired")
        root = Path(ctx.repo_root).resolve()
        proposals: list[SupersessionProposal] = []
        for ref, footprint in curated_footprints(ctx.store, ctx.scope):
            if not footprint:
                continue  # a belief with no footprint has no code to vanish
            missing = tuple(f for f in footprint if not (root / f).exists())
            if len(missing) == len(footprint):
                proposals.append(
                    SupersessionProposal(
                        memory_id=str(ref.memory_id),
                        reason=f"all {len(footprint)} footprint file(s) removed from the codebase",
                        evidence=missing,
                    )
                )
        if not proposals:
            return PassOutcome(summary="no supersession proposals")
        return PassOutcome(
            summary=f"proposed superseding {len(proposals)} belief(s)",
            details={"proposals": [p.as_dict() for p in proposals]},
        )
