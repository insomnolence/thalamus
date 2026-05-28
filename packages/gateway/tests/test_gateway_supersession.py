"""Belief supersession at recall (§13.18 R1): current truth wins the slots, history is
shown with its reason — never hidden, never deleted."""

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
    Supersession,
    TenantId,
)
from thalamus.gateway import Gateway, SupersededDemotingRetriever
from thalamus.retrieval import L0Retriever
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 5, 27, tzinfo=UTC)


def _record(mid: str, content: str) -> MemoryRecord:
    return MemoryRecord(
        MemoryId(mid), Hemisphere.EXPERIENTIAL, "decision", content, SCOPE, NOW,
        metadata={"source": "curated"},
    )


def _scored(record: MemoryRecord, score: float) -> ScoredMemory:
    return ScoredMemory(record=record, score=score, features={"relevance": score})


class _StubRetriever:
    """Returns a fixed candidate pool (highest score first) — isolates the demotion logic."""

    def __init__(self, candidates: list[ScoredMemory]) -> None:
        self._candidates = candidates

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        return RetrievalResult(cue=cue, candidates=self._candidates, shown=self._candidates[:k])


def _cue() -> Cue:
    return Cue(text="q", scope=SCOPE)


def test_demotion_promotes_current_truth_over_a_higher_scored_superseded_belief() -> None:
    old = _record("old", "used X")
    new = _record("new", "use Y now")
    # The superseded belief scores HIGHER — without demotion it would take the only slot.
    stub = _StubRetriever([_scored(old, 0.9), _scored(new, 0.5)])
    superseded = {old.ref: Supersession(MemoryId("new"), "switched to Y", NOW)}

    result = SupersededDemotingRetriever(stub, superseded).retrieve(_cue(), k=1)

    assert [s.record.memory_id for s in result.shown] == [MemoryId("new")]
    assert [s.record.memory_id for s in result.candidates] == [MemoryId("new"), MemoryId("old")]


def test_demotion_is_a_no_op_when_nothing_is_superseded() -> None:
    old = _record("old", "used X")
    stub = _StubRetriever([_scored(old, 0.9)])
    result = SupersededDemotingRetriever(stub, {}).retrieve(_cue(), k=5)
    assert [s.record.memory_id for s in result.shown] == [MemoryId("old")]


def test_gateway_surfaces_superseded_belief_with_its_reason() -> None:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    old = _record("old", "we use the lexical overlap signal")
    new = _record("new", "we use the footprint overlap signal")
    for record in (old, new):
        store.add(record, encoder.encode([record.content])[0])
    superseded = {
        old.ref: Supersession(MemoryId("new"), "lexical under-counted real usage", NOW)
    }
    retriever = SupersededDemotingRetriever(
        L0Retriever(encoder, store, now=lambda: NOW), superseded
    )
    gateway = Gateway(retriever, superseded=superseded)

    payload = gateway.recall(prompt="which usage signal do we use", scope=SCOPE)

    by_id = {item.memory_id: item for item in payload.memories}
    assert by_id[MemoryId("new")].superseded is None  # current truth, unmarked
    note = by_id[MemoryId("old")].superseded
    assert note is not None
    assert note.superseded_by == MemoryId("new")
    assert note.reason == "lexical under-counted real usage"
    # Current truth ranks ahead of the superseded belief in the shown order.
    order = [item.memory_id for item in payload.memories]
    assert order.index(MemoryId("new")) < order.index(MemoryId("old"))

    rendered = payload.render()
    assert "[superseded]" in rendered
    assert "⊘ superseded by new" in rendered
    assert "lexical under-counted real usage" in rendered
