"""Tests for the behavioral store (Track I / Architecture B) — the brain's durable usage record.

The load-bearing property is **accumulation by idempotent union**: consolidating the same logs
twice never double-counts, and re-consolidating a *subset* after old log segments are dropped keeps
the previously-folded signal — which is what makes the raw logs a disposable write-ahead buffer."""

from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core.types import EventId, MemoryId, RepoId, Scope, SessionId, TenantId
from thalamus.experiential import (
    InMemoryBehavioralStore,
    consolidate_usage,
    usage_sessions_by_memory,
)
from thalamus.instrumentation import RetrievalEvent, UsageSignal

SCOPE = Scope(TenantId("t"), RepoId("r"))


def _event(eid: str, session: str | None) -> RetrievalEvent:
    return RetrievalEvent(
        event_id=EventId(eid), timestamp=datetime(2026, 6, 17, tzinfo=UTC), scope=SCOPE,
        policy_id="p", cue_text="cue", k_requested=5, candidates=(), shown=(),
        session_id=None if session is None else SessionId(session),
    )


def _sig(eid: str, mid: str, used: bool) -> UsageSignal:
    return UsageSignal(EventId(eid), MemoryId(mid), "footprint", 1.0 if used else 0.0, used)


def test_usage_sessions_by_memory_returns_the_distinct_session_sets() -> None:
    events = [_event("e1", "s1"), _event("e2", "s2"), _event("e3", None)]
    signals = [
        _sig("e1", "m-a", True), _sig("e2", "m-a", True),  # m-a used in two sessions
        _sig("e1", "m-b", False),  # not used → ignored
        _sig("e3", "m-c", True),  # unkeyed event → skipped
    ]
    sessions = usage_sessions_by_memory(events, signals)
    assert sessions == {MemoryId("m-a"): {SessionId("s1"), SessionId("s2")}}


def test_record_usage_unions_and_weights_count_distinct_sessions() -> None:
    store = InMemoryBehavioralStore()
    store.record_usage({MemoryId("m-a"): {SessionId("s1")}})
    store.record_usage({MemoryId("m-a"): {SessionId("s1"), SessionId("s2")}})  # s1 re-added
    assert store.usage_weights() == {MemoryId("m-a"): 2.0}  # distinct, not 3


def test_consolidate_usage_folds_logs_and_reports_count() -> None:
    store = InMemoryBehavioralStore()
    events = [_event("e1", "s1"), _event("e2", "s2")]
    signals = [_sig("e1", "m-a", True), _sig("e2", "m-b", True)]
    assert consolidate_usage(store, events, signals) == 2
    assert store.usage_weights() == {MemoryId("m-a"): 1.0, MemoryId("m-b"): 1.0}


def test_consolidating_twice_does_not_double_count() -> None:
    store = InMemoryBehavioralStore()
    events = [_event("e1", "s1")]
    signals = [_sig("e1", "m-a", True)]
    consolidate_usage(store, events, signals)
    consolidate_usage(store, events, signals)  # same logs again
    assert store.usage_weights() == {MemoryId("m-a"): 1.0}


def test_subset_reconsolidation_keeps_prior_signal_so_logs_are_disposable() -> None:
    # Accumulate from the full logs, then re-consolidate only a *later* slice (as if the older
    # segment was dropped by rotation). The earlier session must survive in the durable store.
    store = InMemoryBehavioralStore()
    old = ([_event("e1", "s1")], [_sig("e1", "m-a", True)])
    new = ([_event("e2", "s2")], [_sig("e2", "m-a", True)])
    consolidate_usage(store, *old)
    consolidate_usage(store, *new)  # old segment now "dropped" — only the new slice is read
    assert store.usage_weights() == {MemoryId("m-a"): 2.0}  # s1 (from the dropped log) retained
