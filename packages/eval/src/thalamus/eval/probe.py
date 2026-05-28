"""Unlabeled-probe corpus eval — L1 on real session questions.

Transcripts (see :mod:`thalamus.eval.transcripts`) give us a corpus of *real* user
questions, but no labels for which memory should surface. So instead of recall@k /
precision@k against a labeled relevant-set, we measure **surface quality**: did the
retriever find anything confident? How confident on average? And — crucially — how
much *lift* does the brain provide over a brain-off floor?

The interface mirrors :mod:`thalamus.eval.harness` (``evaluate`` + ``compare`` over
named retrievers) so the same ablation discipline applies: pass
``{"brain-off": NullRetriever(), "L0": l0, ...}`` and every rung is scored on the
same probe set.

This is strictly an **L1** instrument. It answers "would the brain have surfaced
anything confident on the questions actually asked"; it does **not** answer "did
that surfacing make the agent's outcomes better" — that needs Tier-2 outcome
volume (§13.20 / proxy↔truth).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean, median

from thalamus.core.protocols import Retriever
from thalamus.core.types import Cue, MemoryId, Scope
from thalamus.eval.transcripts import TranscriptProbe


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """One probe's result on one retriever."""

    probe: TranscriptProbe
    shown: tuple[MemoryId, ...]
    top_score: float  # 0.0 when nothing surfaced
    n_above_threshold: int  # how many of `shown` scored ≥ threshold


@dataclass(frozen=True, slots=True)
class ProbeReport:
    """Aggregate metrics for one retriever over a probe corpus."""

    label: str
    n_probes: int
    k: int
    threshold: float
    surface_rate: float  # fraction with ≥ 1 result at or above ``threshold``
    mean_top_score: float
    median_top_score: float
    p90_top_score: float


def evaluate_probes(
    retriever: Retriever,
    probes: Sequence[TranscriptProbe],
    *,
    scope: Scope,
    k: int = 5,
    threshold: float = 0.0,
    label: str = "retriever",
) -> tuple[ProbeReport, list[ProbeOutcome]]:
    """Run ``retriever`` over ``probes`` and report surface-quality metrics.

    ``threshold`` is the relevance floor a top-1 hit must clear to count as "surfaced"
    for the ``surface_rate`` metric. ``0.0`` accepts any nonzero score; raise it (e.g.
    ``0.5``) to be stricter about what counts as confident.
    """
    outcomes: list[ProbeOutcome] = []
    for probe in probes:
        cue = Cue(text=probe.prompt, scope=scope)
        result = retriever.retrieve(cue, k)
        shown = tuple(item.record.memory_id for item in result.shown)
        top_score = result.shown[0].score if result.shown else 0.0
        n_above = sum(1 for item in result.shown if item.score >= threshold)
        outcomes.append(
            ProbeOutcome(
                probe=probe, shown=shown, top_score=top_score, n_above_threshold=n_above
            )
        )
    return _aggregate(label, outcomes, k=k, threshold=threshold), outcomes


def compare_probes(
    retrievers: Mapping[str, Retriever],
    probes: Sequence[TranscriptProbe],
    *,
    scope: Scope,
    k: int = 5,
    threshold: float = 0.0,
) -> dict[str, ProbeReport]:
    """Score each named retriever on the same probes — the ablation switch.

    Idiomatic use: ``{"brain-off": NullRetriever(), "L0": real_chain}`` to see the
    lift the brain provides over the floor."""
    return {
        label: evaluate_probes(
            retriever, probes, scope=scope, k=k, threshold=threshold, label=label
        )[0]
        for label, retriever in retrievers.items()
    }


def _aggregate(
    label: str, outcomes: Sequence[ProbeOutcome], *, k: int, threshold: float
) -> ProbeReport:
    if not outcomes:
        return ProbeReport(
            label=label, n_probes=0, k=k, threshold=threshold,
            surface_rate=0.0, mean_top_score=0.0, median_top_score=0.0, p90_top_score=0.0,
        )
    top_scores = [o.top_score for o in outcomes]
    surfaced = sum(1 for o in outcomes if o.top_score >= threshold and o.shown)
    return ProbeReport(
        label=label,
        n_probes=len(outcomes),
        k=k,
        threshold=threshold,
        surface_rate=surfaced / len(outcomes),
        mean_top_score=fmean(top_scores),
        median_top_score=median(top_scores),
        p90_top_score=_percentile(top_scores, 0.90),
    )


def _percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (no interpolation) — robust on small/skewed corpora."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(round(q * (len(ordered) - 1)))))
    return ordered[rank]
