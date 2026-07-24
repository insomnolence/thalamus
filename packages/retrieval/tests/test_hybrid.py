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
from thalamus.retrieval import (
    HybridRetriever,
    L0Retriever,
    LexicalRetriever,
    UsageWeightedRetriever,
    UsageWeightsRef,
)
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))
NOW = datetime(2026, 6, 14, tzinfo=UTC)


def _record(mid: str, content: str) -> MemoryRecord:
    return MemoryRecord(
        memory_id=MemoryId(mid),
        hemisphere=Hemisphere.EXPERIENTIAL,
        kind="episode",
        content=content,
        scope=SCOPE,
        created_at=NOW,
    )


def _scored(mid: str, score: float) -> ScoredMemory:
    return ScoredMemory(record=_record(mid, mid), score=score)


class _Stub:
    """A retriever that returns a fixed candidate ordering — to test the fusion logic exactly."""

    def __init__(self, ordered: list[ScoredMemory]) -> None:
        self._ordered = ordered

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        return RetrievalResult(cue=cue, candidates=self._ordered, shown=self._ordered[:k])


class _ReverseCandidates:
    """A hypothetical reordering decorator that preserves features but stamps no rank itself."""

    def __init__(self, inner: HybridRetriever) -> None:
        self._inner = inner

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        result = self._inner.retrieve(cue, k)
        candidates = list(reversed(result.candidates))
        return RetrievalResult(
            cue=result.cue,
            candidates=candidates,
            shown=candidates[: max(k, 0)],
            event_id=result.event_id,
        )


def test_rrf_rewards_a_memory_found_by_both_legs() -> None:
    semantic = _Stub([_scored("A", 2.0), _scored("B", 1.5), _scored("C", 1.0)])
    lexical = _Stub([_scored("D", 5.0), _scored("B", 4.0)])
    fused = HybridRetriever(semantic, lexical).retrieve(Cue(text="q", scope=SCOPE), k=4)
    order = [s.record.memory_id for s in fused.shown]
    assert order[0] == MemoryId("B")  # only memory in BOTH lists -> highest RRF


def test_a_lexical_only_hit_is_surfaced_with_its_native_score() -> None:
    semantic = _Stub([_scored("A", 2.0), _scored("C", 1.0)])
    lexical = _Stub([_scored("D", 5.0)])  # D is invisible to the semantic leg
    fused = HybridRetriever(semantic, lexical).retrieve(Cue(text="q", scope=SCOPE), k=5)
    by_id = {s.record.memory_id: s for s in fused.candidates}
    assert MemoryId("D") in by_id  # recovered — the whole point of hybrid
    assert by_id[MemoryId("D")].score == 5.0  # keeps its lexical (native) score for display
    assert by_id[MemoryId("A")].score == 2.0  # semantic hit keeps its semantic score
    assert "rrf" in by_id[MemoryId("D")].features


def test_features_record_each_leg_rank() -> None:
    semantic = _Stub([_scored("A", 2.0), _scored("B", 1.5)])
    lexical = _Stub([_scored("B", 4.0), _scored("D", 3.0)])
    fused = HybridRetriever(semantic, lexical).retrieve(Cue(text="q", scope=SCOPE), k=5)
    b = next(s for s in fused.candidates if s.record.memory_id == MemoryId("B"))
    assert b.features["semantic_rank"] == 2.0  # B is 2nd in the semantic list
    assert b.features["lexical_rank"] == 1.0  # and 1st in the lexical list


def test_final_hybrid_rank_is_preserved_through_a_downstream_rrf_rung() -> None:
    semantic = _Stub([_scored("A", 3.0), _scored("B", 2.0), _scored("C", 1.0)])
    lexical = _Stub([_scored("B", 4.0)])
    hybrid = HybridRetriever(semantic, lexical)

    hybrid_result = hybrid.retrieve(Cue(text="q", scope=SCOPE), k=3)
    hybrid_by_id = {s.record.memory_id: s for s in hybrid_result.candidates}
    assert [s.record.memory_id for s in hybrid_result.candidates] == [
        MemoryId("B"),
        MemoryId("A"),
        MemoryId("C"),
    ]
    assert hybrid_by_id[MemoryId("B")].features["initial_relevance_rank"] == 1.0
    assert hybrid_by_id[MemoryId("A")].features["initial_relevance_rank"] == 2.0
    assert hybrid_by_id[MemoryId("C")].features["initial_relevance_rank"] == 3.0
    assert all("fusion_score" not in s.features for s in hybrid_result.candidates)

    # A future decorator could reorder candidates without knowing the RRF feature contract. The
    # downstream rung must still see Hybrid's base ranks, not recreate them from this reversed list.
    reordered = _ReverseCandidates(hybrid)
    reordered_result = reordered.retrieve(Cue(text="q", scope=SCOPE), k=3)
    assert [s.record.memory_id for s in reordered_result.candidates] == [
        MemoryId("C"),
        MemoryId("A"),
        MemoryId("B"),
    ]
    composed = UsageWeightedRetriever(reordered, UsageWeightsRef({MemoryId("B"): 9.0}))
    result = composed.retrieve(Cue(text="q", scope=SCOPE), k=3)
    by_id = {s.record.memory_id: s for s in result.candidates}
    assert result.candidates[0].record.memory_id == MemoryId("B")
    assert by_id[MemoryId("B")].features["initial_relevance_rank"] == 1.0
    assert by_id[MemoryId("A")].features["initial_relevance_rank"] == 2.0
    assert by_id[MemoryId("C")].features["initial_relevance_rank"] == 3.0


def test_end_to_end_hybrid_recovers_an_exact_identifier_the_vector_buries() -> None:
    # A real semantic (L0 over a deterministic encoder) + real lexical leg. The query names an
    # exact identifier present in one memory; the lexical leg ranks it #1, so hybrid surfaces it.
    encoder = DeterministicEncoder(dim=64)
    store = InMemoryStore(dim=64)
    memories = [
        _record("target", "the SupersededDemotingRetriever promotes current truth"),
        *[_record(f"noise{i}", f"unrelated note number {i} about the system") for i in range(8)],
    ]
    for record in memories:
        store.add(record, encoder.encode([record.content])[0])
    hybrid = HybridRetriever(L0Retriever(encoder, store), LexicalRetriever(store))
    result = hybrid.retrieve(Cue(text="SupersededDemotingRetriever", scope=SCOPE), k=3)
    assert MemoryId("target") in {s.record.memory_id for s in result.shown}
