"""The eval harness: score a retriever on a benchmark, and compare retrievers.

``compare`` is the **ablation switch** the design requires (OLR §13.20): pass
``{"brain-off": NullRetriever(), "L0": l0, "L0+rung": ...}`` and every rung is
scored on the same cases, so any layer can be measured against the one below it
(and against the brain-off floor). Depends only on the ``core.Retriever`` protocol.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean

from thalamus.core.protocols import Retriever
from thalamus.core.types import Cue, RetrievalResult
from thalamus.eval.benchmark import BenchmarkCase
from thalamus.eval.metrics import hit_at_k, precision_at_k, recall_at_k, reciprocal_rank


@dataclass(frozen=True, slots=True)
class EvalReport:
    k: int
    n_cases: int
    recall_at_k: float
    precision_at_k: float
    mrr: float
    hit_rate: float


def evaluate(retriever: Retriever, cases: Sequence[BenchmarkCase], k: int) -> EvalReport:
    if not cases:
        return EvalReport(
            k=k, n_cases=0, recall_at_k=0.0, precision_at_k=0.0, mrr=0.0, hit_rate=0.0
        )
    recalls: list[float] = []
    precisions: list[float] = []
    rrs: list[float] = []
    hits: list[float] = []
    for case in cases:
        shown = [scored.record.memory_id for scored in retriever.retrieve(case.cue, k).shown]
        recalls.append(recall_at_k(shown, case.relevant, k))
        precisions.append(precision_at_k(shown, case.relevant, k))
        rrs.append(reciprocal_rank(shown, case.relevant))
        hits.append(hit_at_k(shown, case.relevant, k))
    return EvalReport(
        k=k,
        n_cases=len(cases),
        recall_at_k=fmean(recalls),
        precision_at_k=fmean(precisions),
        mrr=fmean(rrs),
        hit_rate=fmean(hits),
    )


def compare(
    retrievers: Mapping[str, Retriever], cases: Sequence[BenchmarkCase], k: int
) -> dict[str, EvalReport]:
    """Score each named retriever on the same cases — the ablation switch."""
    return {name: evaluate(retriever, cases, k) for name, retriever in retrievers.items()}


class NullRetriever:
    """The 'brain-off' baseline: surfaces nothing. The floor every layer must beat."""

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        return RetrievalResult(cue=cue, candidates=[], shown=[])
