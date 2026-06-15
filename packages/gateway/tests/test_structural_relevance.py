"""Tests for StructuralRelevanceRetriever — query-local boost of memories about the cue's code."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

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
    StructuralRef,
    TenantId,
)
from thalamus.gateway import StructuralRelevanceRetriever
from thalamus.structural import InMemoryCrossLinkIndex, ScoredNode, StructuralNode

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 6, 15, tzinfo=UTC)
CUE = Cue(text="how does the store work", scope=SCOPE)


def _mem(mid: str, score: float) -> ScoredMemory:
    record = MemoryRecord(
        MemoryId(mid), Hemisphere.EXPERIENTIAL, "decision", mid, SCOPE, NOW
    )
    return ScoredMemory(record=record, score=score, features={"relevance": score})


class _Inner:
    def __init__(self, candidates: list[ScoredMemory]) -> None:
        self._candidates = candidates

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        return RetrievalResult(cue=cue, candidates=self._candidates, shown=self._candidates[:k])


class _StructRetr:
    """A structural retriever stub returning fixed (node_id, score) hits for the cue."""

    corpus = "code"

    def __init__(self, hits: Sequence[tuple[str, float]]) -> None:
        self._hits = hits

    def retrieve(self, cue: Cue, k: int) -> list[ScoredNode]:
        return [
            ScoredNode(node=StructuralNode(node_id=nid, kind="module", label=nid, scope=SCOPE),
                       score=score)
            for nid, score in self._hits
        ][:k]


def _order(result: RetrievalResult) -> list[str]:
    return [str(c.record.memory_id) for c in result.candidates]


def test_boosts_memory_linked_to_the_cue_structural_hit() -> None:
    # retained:c is relevance-rank 3, but it's cross-linked to module:store — which the cue's
    # structural retrieval surfaces — so it's promoted ("the why behind the code in your query").
    inner = _Inner([_mem("a", 2.0), _mem("b", 1.5), _mem("retained:c", 1.0)])
    links = InMemoryCrossLinkIndex()
    links.link(MemoryRef(SCOPE, MemoryId("retained:c")), StructuralRef(SCOPE, "module:store"))
    retriever = StructuralRelevanceRetriever(inner, links, [_StructRetr([("module:store", 0.9)])])
    result = retriever.retrieve(CUE, k=1)
    assert _order(result)[0] == "retained:c"
    assert result.candidates[0].features["structural_relevance_rank"] == 1.0
    assert result.candidates[0].score == 1.0  # native relevance score preserved


def test_no_op_when_no_candidate_links_to_the_cue_code() -> None:
    inner = _Inner([_mem("a", 2.0), _mem("b", 1.5)])
    links = InMemoryCrossLinkIndex()  # nothing cross-linked
    retriever = StructuralRelevanceRetriever(inner, links, [_StructRetr([("module:store", 0.9)])])
    result = retriever.retrieve(CUE, k=2)
    assert _order(result) == ["a", "b"]  # relevance order untouched


def test_no_op_without_structural_hits() -> None:
    inner = _Inner([_mem("a", 2.0)])
    links = InMemoryCrossLinkIndex()
    links.link(MemoryRef(SCOPE, MemoryId("a")), StructuralRef(SCOPE, "module:store"))
    result = StructuralRelevanceRetriever(inner, links, [_StructRetr([])]).retrieve(CUE, k=1)
    assert _order(result) == ["a"]  # no anchors → unchanged
