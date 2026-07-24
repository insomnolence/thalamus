"""Tests for StructuralCentralityRetriever — RRF re-rank of relevance by structural centrality."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from thalamus.core.types import (
    Cue,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    MemoryRef,
    RepoId,
    RetrievalResult,
    Scope,
    ScoredMemory,
    TenantId,
)
from thalamus.retrieval import (
    CentralityWeightsRef,
    StructuralCentralityRetriever,
    UsageWeightedRetriever,
    UsageWeightsRef,
)

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))
NOW = datetime(2026, 6, 16, tzinfo=UTC)
CUE = Cue(text="q", scope=SCOPE)


def _ref(mid: str) -> MemoryRef:
    return MemoryRef(scope=SCOPE, memory_id=MemoryId(mid))


def _scored(mid: str, score: float) -> ScoredMemory:
    record = MemoryRecord(
        memory_id=MemoryId(mid), hemisphere=Hemisphere.EXPERIENTIAL, kind="episode",
        content=mid, scope=SCOPE, created_at=NOW,
    )
    return ScoredMemory(record=record, score=score, features={"relevance": score})


class _Stub:
    """An inner retriever returning a fixed relevance ordering — to test the re-rank exactly."""

    def __init__(self, ordered: list[ScoredMemory]) -> None:
        self._ordered = ordered

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        return RetrievalResult(cue=cue, candidates=self._ordered, shown=self._ordered[:k])


def _order(result: RetrievalResult) -> list[str]:
    return [str(c.record.memory_id) for c in result.candidates]


def test_well_connected_memory_is_promoted_over_higher_relevance_disconnected() -> None:
    # C is relevance-rank 3 but well-connected to Brain 2 → it rises to the top of the re-rank.
    inner = _Stub([_scored("A", 2.0), _scored("B", 1.5), _scored("C", 1.0)])
    retriever = StructuralCentralityRetriever(inner, CentralityWeightsRef({_ref("C"): 9.0}))
    result = retriever.retrieve(CUE, k=1)
    assert _order(result)[0] == "C"
    assert [str(c.record.memory_id) for c in result.shown] == ["C"]


def test_empty_centrality_preserves_the_relevance_order() -> None:
    # No links anywhere → pure relevance ranking, unchanged (identity for a linkless brain).
    inner = _Stub([_scored("A", 2.0), _scored("B", 1.5), _scored("C", 1.0)])
    result = StructuralCentralityRetriever(inner, CentralityWeightsRef()).retrieve(CUE, k=3)
    assert _order(result) == ["A", "B", "C"]


def test_more_central_outranks_less_central() -> None:
    inner = _Stub([_scored("A", 2.0), _scored("B", 1.5), _scored("C", 1.0)])
    weights = CentralityWeightsRef({_ref("B"): 9.0, _ref("C"): 3.0})
    order = _order(StructuralCentralityRetriever(inner, weights).retrieve(CUE, k=3))
    assert order.index("B") < order.index("C")  # B (more connected) above C (less)
    assert order.index("C") < order.index("A")  # both connected above the disconnected A


def test_weight_zero_ablates_the_layer() -> None:
    # weight=0.0 turns the layer off → relevance order is preserved even with centrality present.
    inner = _Stub([_scored("A", 2.0), _scored("B", 1.5), _scored("C", 1.0)])
    weights = CentralityWeightsRef({_ref("C"): 99.0})
    result = StructuralCentralityRetriever(inner, weights, weight=0.0).retrieve(CUE, k=3)
    assert _order(result) == ["A", "B", "C"]


def test_refreshing_the_ref_changes_the_ranking_live() -> None:
    # The mid-serve refresh: swapping the ref's weights re-ranks subsequent retrievals, no rebuild.
    inner = _Stub([_scored("A", 2.0), _scored("B", 1.5), _scored("C", 1.0)])
    ref = CentralityWeightsRef()
    retriever = StructuralCentralityRetriever(inner, ref)
    assert _order(retriever.retrieve(CUE, k=3)) == ["A", "B", "C"]  # cold: relevance order
    ref.refresh({_ref("C"): 9.0})
    assert _order(retriever.retrieve(CUE, k=3))[0] == "C"  # after refresh, C is lifted


def test_native_score_preserved_and_centrality_recorded_in_features() -> None:
    inner = _Stub([_scored("A", 2.0), _scored("C", 1.0)])
    result = StructuralCentralityRetriever(
        inner, CentralityWeightsRef({_ref("C"): 7.0})
    ).retrieve(CUE, k=2)
    by_id = {str(c.record.memory_id): c for c in result.candidates}
    assert by_id["C"].score == 1.0  # native relevance score preserved for the log/display
    assert by_id["C"].features["centrality_rank"] == 1.0
    assert by_id["C"].features["centrality_weight"] == 7.0
    assert "centrality_rank" not in by_id["A"].features  # disconnected memory carries no features


def test_outer_centrality_preserves_inner_usage_contribution() -> None:
    """The live default is centrality(usage(relevance)); both signal legs must survive."""
    inner = _Stub([_scored("A", 3.0), _scored("B", 2.0), _scored("C", 1.0)])
    usage = UsageWeightsRef({MemoryId("C"): 9.0})
    centrality = CentralityWeightsRef({_ref("B"): 9.0})

    usage_only = UsageWeightedRetriever(inner, usage)
    assert _order(usage_only.retrieve(CUE, k=3)) == ["C", "A", "B"]
    assert _order(StructuralCentralityRetriever(inner, centrality).retrieve(CUE, k=3)) == [
        "B",
        "A",
        "C",
    ]

    result = StructuralCentralityRetriever(usage_only, centrality).retrieve(CUE, k=3)
    assert _order(result) == ["B", "C", "A"]
    by_id = {str(c.record.memory_id): c for c in result.candidates}
    assert "usage_rank" in by_id["C"].features
    assert "centrality_rank" in by_id["B"].features
    # These exact sums are the regression guard: the buggy outer rung recomputed relevance plus
    # centrality and discarded C's inner usage term, while still producing the same B,C,A order.
    assert by_id["C"].features["fusion_score"] == pytest.approx(1 / 63 + 1 / 61)
    assert by_id["B"].features["fusion_score"] == pytest.approx(1 / 62 + 1 / 61)


def test_usage_leg_changes_composed_order_when_outer_signal_is_weaker() -> None:
    inner = _Stub([_scored("A", 3.0), _scored("B", 2.0), _scored("C", 1.0)])
    centrality = CentralityWeightsRef({_ref("B"): 9.0})
    outer_only = StructuralCentralityRetriever(inner, centrality, weight=0.75)
    composed = StructuralCentralityRetriever(
        UsageWeightedRetriever(inner, UsageWeightsRef({MemoryId("C"): 9.0})),
        centrality,
        weight=0.75,
    )
    assert _order(outer_only.retrieve(CUE, k=3)) == ["B", "A", "C"]
    assert _order(composed.retrieve(CUE, k=3)) == ["C", "B", "A"]
