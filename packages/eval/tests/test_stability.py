"""Usage stability: per-memory "used vs. surfaced-but-ignored" as a *stable* signal.

Records are built by hand to pin the semantics — clean two-classing (separation),
the min-surfaced eligibility cut, cross-session reuse, and the "used if any signal"
dedup it shares with ``utility_at_k``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from thalamus.core.types import EventId, MemoryId, RepoId, Scope, SessionId, TenantId
from thalamus.eval import UsageStabilityReport, usage_stability
from thalamus.instrumentation import RetrievalEvent, ShownItem, UsageSignal

SCOPE = Scope(TenantId("t1"), RepoId("r1"))
NOW = datetime(2026, 5, 25, tzinfo=UTC)


def _event(event_id: str, session: str, shown_ids: Sequence[str]) -> RetrievalEvent:
    return RetrievalEvent(
        event_id=EventId(event_id),
        timestamp=NOW,
        scope=SCOPE,
        policy_id="L0",
        cue_text="q",
        k_requested=len(shown_ids),
        candidates=[],
        shown=[ShownItem(MemoryId(m), rank=i, propensity=1.0) for i, m in enumerate(shown_ids)],
        session_id=SessionId(session),
    )


def _signal(event_id: str, memory_id: str, *, used: bool, kind: str = "footprint") -> UsageSignal:
    return UsageSignal(EventId(event_id), MemoryId(memory_id), kind, 1.0 if used else 0.0, used)


def test_separates_reliably_used_from_ignored_and_excludes_one_shots() -> None:
    events = [_event(f"e{i}", "s1", ["m"]) for i in range(1, 8)]
    signals = [
        _signal("e1", "used", used=True), _signal("e2", "used", used=True),  # rate 1.0
        _signal("e3", "ignored", used=False), _signal("e4", "ignored", used=False),  # rate 0.0
        _signal("e5", "mixed", used=True), _signal("e6", "mixed", used=False),  # rate 0.5
        _signal("e7", "oneshot", used=True),  # surfaced once -> below threshold, excluded
    ]
    report = usage_stability(events, signals, min_surfaced=2)
    assert report.n_eligible == 3  # oneshot dropped
    assert (report.n_reliable, report.n_ignored, report.n_mixed) == (1, 1, 1)
    assert report.mean_rate == pytest.approx(0.5)  # mean(1.0, 0.0, 0.5)
    assert report.separation == pytest.approx(2 / 3)  # 2 of 3 cleanly classed


def test_cross_session_reuse_counts_distinct_sessions_only() -> None:
    # m_core is used across two distinct sessions; m_local is used twice in one session.
    events = [
        _event("e1", "s1", ["m_core"]), _event("e2", "s2", ["m_core"]),
        _event("e3", "s3", ["m_local"]), _event("e4", "s3", ["m_local"]),
    ]
    signals = [
        _signal("e1", "m_core", used=True), _signal("e2", "m_core", used=True),
        _signal("e3", "m_local", used=True), _signal("e4", "m_local", used=True),
    ]
    report = usage_stability(events, signals, min_surfaced=2)
    assert report.n_reused == 1  # only m_core spans >= 2 sessions
    assert report.max_reuse == 2


def test_used_if_any_signal_kind_marks_the_pair_used() -> None:
    # The same (event, memory) carries both a citation miss and a footprint hit — the dedup
    # must treat the pair as one surfaced instance that was used (matching utility_at_k).
    events = [_event("e1", "s1", ["m"]), _event("e2", "s1", ["m"])]
    signals = [
        _signal("e1", "m", used=False, kind="citation"),
        _signal("e1", "m", used=True, kind="footprint"),
        _signal("e2", "m", used=True),
    ]
    report = usage_stability(events, signals, min_surfaced=2)
    assert report.n_eligible == 1
    assert report.n_reliable == 1  # rate 1.0 over the two distinct events
    assert report.n_ignored == 0


def test_empty_inputs_are_honest_zeros() -> None:
    assert usage_stability([], [], min_surfaced=2) == UsageStabilityReport(
        min_surfaced=2, n_eligible=0, n_reliable=0, n_ignored=0, n_mixed=0,
        mean_rate=0.0, separation=0.0, n_reused=0, max_reuse=0,
    )
