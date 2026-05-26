from __future__ import annotations

from datetime import UTC, datetime

import pytest
from thalamus.core.types import EventId, MemoryId, RepoId, Scope, SessionId, TenantId
from thalamus.eval import (
    join_proxy_truth,
    proxy_truth,
    session_proxy_truth,
    session_utility,
)
from thalamus.instrumentation import RetrievalEvent, ShownItem, UsageSignal

SCOPE = Scope(tenant_id=TenantId("t"), repo_id=RepoId("r"))
NOW = datetime(2026, 5, 25, tzinfo=UTC)


def test_proxy_aligned_with_truth() -> None:
    # successes carry higher utility than failures -> aligned, not gamed
    report = proxy_truth([(0.9, True), (0.8, True), (0.2, False)])
    assert report.alignment == pytest.approx(0.85 - 0.2)
    assert report.success_rate == pytest.approx(2 / 3)
    assert report.reward_hacking_suspected is False


def test_reward_hacking_flagged() -> None:
    # utility high everywhere but it does not separate success from failure
    report = proxy_truth([(0.9, True), (0.9, False), (0.9, False)])
    assert report.alignment == 0.0
    assert report.mean_utility == pytest.approx(0.9)
    assert report.reward_hacking_suspected is True


def test_empty_units() -> None:
    report = proxy_truth([])
    assert report.n_units == 0
    assert report.reward_hacking_suspected is False


def _event(eid: str, session: str, shown_ids: list[str]) -> RetrievalEvent:
    return RetrievalEvent(
        event_id=EventId(eid), timestamp=NOW, scope=SCOPE, policy_id="L0", cue_text="q",
        k_requested=len(shown_ids),
        candidates=[],
        shown=[ShownItem(MemoryId(m), rank=i, propensity=1.0) for i, m in enumerate(shown_ids)],
        session_id=SessionId(session),
    )


def _signal(eid: str, mid: str, *, used: bool) -> UsageSignal:
    return UsageSignal(EventId(eid), MemoryId(mid), "overlap", 1.0 if used else 0.0, used)


def test_session_utility_groups_by_session() -> None:
    events = [
        _event("e1", "s1", ["a", "b"]),  # a used, b not -> 0.5
        _event("e2", "s1", ["c"]),  # c used -> 1.0
        _event("e3", "s2", ["d"]),  # no usage captured -> excluded
    ]
    signals = [
        _signal("e1", "a", used=True), _signal("e1", "b", used=False),
        _signal("e2", "c", used=True),
    ]
    by_session = session_utility(events, signals, k=2)
    assert by_session == {SessionId("s1"): pytest.approx(0.75)}  # s2 has no outcome -> dropped


def test_join_inner_joins_sessions_present_on_both_sides() -> None:
    tier1 = {SessionId("s1"): 0.9, SessionId("s2"): 0.2, SessionId("s3"): 0.5}
    tier2 = {SessionId("s1"): True, SessionId("s2"): False}  # s3 has no Tier-2 label
    units = join_proxy_truth(tier1, tier2)
    assert sorted(units) == [(0.2, False), (0.9, True)]  # s3 dropped (missing truth, not zero)


def test_session_proxy_truth_joins_then_correlates() -> None:
    tier1 = {SessionId("s1"): 0.9, SessionId("s2"): 0.2, SessionId("s3"): 0.5}
    tier2 = {SessionId("s1"): True, SessionId("s2"): False}
    report = session_proxy_truth(tier1, tier2)
    assert report.n_units == 2  # only the two joined sessions
    assert report.alignment == pytest.approx(0.9 - 0.2)
    assert report.reward_hacking_suspected is False
