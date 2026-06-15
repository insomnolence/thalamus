"""Tests for UsageWeightedRetriever — RRF re-rank of relevance by behavioral usage."""

from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core.types import (
    Cue,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    RetrievalResult,
    Scope,
    ScoredMemory,
    TenantId,
)
from thalamus.retrieval import UsageWeightedRetriever

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))
NOW = datetime(2026, 6, 15, tzinfo=UTC)
CUE = Cue(text="q", scope=SCOPE)


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


def test_reliably_used_memory_is_promoted_over_higher_relevance_unused() -> None:
    # C is relevance-rank 3 but heavily used → it should rise to the top of the re-rank.
    inner = _Stub([_scored("A", 2.0), _scored("B", 1.5), _scored("C", 1.0)])
    retriever = UsageWeightedRetriever(inner, {MemoryId("C"): 5.0})
    result = retriever.retrieve(CUE, k=1)
    assert _order(result)[0] == "C"
    assert [str(c.record.memory_id) for c in result.shown] == ["C"]


def test_empty_usage_preserves_the_relevance_order() -> None:
    # No usage signal anywhere → pure relevance ranking, unchanged (ablation/identity).
    inner = _Stub([_scored("A", 2.0), _scored("B", 1.5), _scored("C", 1.0)])
    result = UsageWeightedRetriever(inner, {}).retrieve(CUE, k=3)
    assert _order(result) == ["A", "B", "C"]


def test_more_used_outranks_less_used() -> None:
    inner = _Stub([_scored("A", 2.0), _scored("B", 1.5), _scored("C", 1.0)])
    retriever = UsageWeightedRetriever(inner, {MemoryId("B"): 5.0, MemoryId("C"): 2.0})
    order = _order(retriever.retrieve(CUE, k=3))
    assert order.index("B") < order.index("C")  # B (more sessions) above C (fewer)
    assert order.index("C") < order.index("A")  # both used above the never-used A


def test_native_score_preserved_and_usage_recorded_in_features() -> None:
    inner = _Stub([_scored("A", 2.0), _scored("C", 1.0)])
    result = UsageWeightedRetriever(inner, {MemoryId("C"): 3.0}).retrieve(CUE, k=2)
    by_id = {str(c.record.memory_id): c for c in result.candidates}
    assert by_id["C"].score == 1.0  # native relevance score preserved for the log/display
    assert by_id["C"].features["usage_rank"] == 1.0
    assert by_id["C"].features["usage_weight"] == 3.0
    assert "usage_rank" not in by_id["A"].features  # never-used memory carries no usage features
