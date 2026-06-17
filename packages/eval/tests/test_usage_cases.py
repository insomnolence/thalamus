from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core.types import EventId, MemoryId, RepoId, Scope, TenantId
from thalamus.eval import cases_from_usage
from thalamus.instrumentation import RetrievalEvent, UsageSignal

SCOPE = Scope(TenantId("t"), RepoId("r"))


def _event(eid: str, cue: str) -> RetrievalEvent:
    return RetrievalEvent(
        event_id=EventId(eid), timestamp=datetime(2026, 6, 17, tzinfo=UTC), scope=SCOPE,
        policy_id="p", cue_text=cue, k_requested=5, candidates=(), shown=(),
    )


def _sig(eid: str, mid: str, used: bool) -> UsageSignal:
    return UsageSignal(
        event_id=EventId(eid), memory_id=MemoryId(mid), kind="footprint", value=1.0, used=used
    )


def test_cases_from_usage_joins_used_memories_to_their_cue() -> None:
    events = [_event("e1", "how does auth work"), _event("e2", "a recall nothing was used from")]
    signals = [_sig("e1", "m-a", True), _sig("e1", "m-b", False), _sig("e2", "m-c", False)]
    cases = cases_from_usage(events, signals)
    assert len(cases) == 1  # e2 had no used memory → no case
    assert cases[0].cue.text == "how does auth work"
    assert cases[0].relevant == frozenset({MemoryId("m-a")})


def test_cases_from_usage_dedupes_and_ignores_unknown_events() -> None:
    events = [_event("e1", "q")]
    signals = [_sig("e1", "m", True), _sig("e1", "m", True), _sig("zz", "m2", True)]
    cases = cases_from_usage(events, signals)
    assert len(cases) == 1
    assert cases[0].relevant == frozenset({MemoryId("m")})  # 'zz' has no matching event


def test_cases_from_usage_empty_when_nothing_used() -> None:
    assert cases_from_usage([_event("e1", "q")], [_sig("e1", "m", False)]) == []
