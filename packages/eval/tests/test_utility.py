"""``utility@k`` on real signals: the retrieval-event log joined to the Tier-1
usage log by ``event_id``. Records are built by hand to pin the join semantics,
then the full path is exercised through durable JSONL logs and the gateway loop.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from thalamus.core.types import (
    EventId,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    TenantId,
)
from thalamus.eval import UtilityReport, utility_at_k
from thalamus.gateway import Gateway
from thalamus.instrumentation import (
    InMemoryEventSink,
    InMemoryUsageSink,
    JsonlEventSink,
    JsonlUsageSink,
    LoggingRetriever,
    RetrievalEvent,
    ShownItem,
    UsageSignal,
    read_event_log,
    read_usage_log,
)
from thalamus.retrieval import L0Retriever
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))
NOW = datetime(2026, 5, 25, tzinfo=UTC)


def _event(event_id: str, shown_ids: Sequence[str]) -> RetrievalEvent:
    return RetrievalEvent(
        event_id=EventId(event_id),
        timestamp=NOW,
        scope=SCOPE,
        policy_id="L0",
        cue_text="q",
        k_requested=len(shown_ids),
        candidates=[],
        shown=[ShownItem(MemoryId(m), rank=i, propensity=1.0) for i, m in enumerate(shown_ids)],
    )


def _signal(event_id: str, memory_id: str, *, used: bool) -> UsageSignal:
    value = 1.0 if used else 0.0
    return UsageSignal(EventId(event_id), MemoryId(memory_id), "overlap", value, used)


def test_fraction_of_shown_that_were_used() -> None:
    events = [_event("e1", ["a", "b", "c"])]
    signals = [
        _signal("e1", "a", used=True),
        _signal("e1", "b", used=True),
        _signal("e1", "c", used=False),
    ]
    report = utility_at_k(events, signals, k=3)
    assert report.utility_at_k == pytest.approx(2 / 3)
    assert (report.n_events, report.n_shown, report.n_used) == (1, 3, 2)
    assert report.coverage == 1.0


def test_at_k_truncates_to_top_k_shown() -> None:
    events = [_event("e1", ["a", "b", "c"])]
    # 'c' was used but sits at rank 2 — excluded at k=2; only 'a' counts.
    signals = [_signal("e1", "a", used=True), _signal("e1", "c", used=True)]
    report = utility_at_k(events, signals, k=2)
    assert report.utility_at_k == 0.5
    assert (report.n_shown, report.n_used) == (2, 1)


def test_event_without_captured_outcome_is_excluded_not_zero() -> None:
    # e2 surfaced a memory but its outcome was never recorded (no signals) —
    # missing data, not zero utility. It must not drag the metric down.
    events = [_event("e1", ["a"]), _event("e2", ["b"])]
    signals = [_signal("e1", "a", used=True)]
    report = utility_at_k(events, signals, k=5)
    assert report.utility_at_k == 1.0
    assert report.n_events == 1  # only the scored event
    assert report.coverage == 0.5  # 1 of 2 surfacing events had an outcome


def _citation(event_id: str, memory_id: str, *, used: bool) -> UsageSignal:
    v = 1.0 if used else 0.0
    return UsageSignal(EventId(event_id), MemoryId(memory_id), "citation", v, used)


def test_citation_only_event_is_missing_data_not_a_scored_zero() -> None:
    # e2's ONLY signal is a citation (record_usage fired, but footprint attribution never ran — e.g.
    # the session committed nothing in window). Citation `used` ~never fires, so scoring e2 would
    # feed a guaranteed zero. It must be treated as missing data, exactly like a no-signal event.
    events = [_event("e1", ["a"]), _event("e2", ["b"])]
    signals = [_signal("e1", "a", used=True), _citation("e2", "b", used=False)]
    report = utility_at_k(events, signals, k=5)
    assert report.utility_at_k == 1.0  # e1 only; e2 is excluded, NOT a 0 dragging it to 0.5
    assert report.n_events == 1
    assert report.coverage == 0.5  # e2 surfaced but had no deterministic outcome


def test_citation_can_mark_a_memory_used_within_a_footprint_scored_event() -> None:
    # e1 has a deterministic outcome (non-citation signal) → scored; a citation may still mark a
    # second memory used. The secondary signal contributes a positive, it just can't define scoring.
    events = [_event("e1", ["a", "b"])]
    signals = [_signal("e1", "a", used=True), _citation("e1", "b", used=True)]
    report = utility_at_k(events, signals, k=2)
    assert report.utility_at_k == 1.0  # both a and b counted used
    assert (report.n_events, report.n_used) == (1, 2)


def test_macro_mean_weights_events_equally() -> None:
    # e1 shows 1 (used) -> 1.0; e2 shows 3 (1 used) -> 1/3. Macro = mean(1, 1/3).
    events = [_event("e1", ["a"]), _event("e2", ["b", "c", "d"])]
    signals = [
        _signal("e1", "a", used=True),
        _signal("e2", "b", used=True),
        _signal("e2", "c", used=False),
        _signal("e2", "d", used=False),
    ]
    report = utility_at_k(events, signals, k=5)
    assert report.utility_at_k == pytest.approx((1.0 + 1 / 3) / 2)
    # pooled counts expose the micro ratio (2/4 = 0.5), distinct from the macro mean
    assert (report.n_shown, report.n_used) == (4, 2)


def test_all_unused_is_zero_but_scored() -> None:
    report = utility_at_k([_event("e1", ["a", "b"])], [_signal("e1", "a", used=False)], k=2)
    assert report.utility_at_k == 0.0
    assert report.n_events == 1  # outcome was captured, so it counts
    assert report.coverage == 1.0


def test_empty_inputs() -> None:
    report = utility_at_k([], [], k=3)
    assert report == UtilityReport(
        k=3, n_events=0, n_shown=0, n_used=0, utility_at_k=0.0, coverage=0.0
    )


def test_computed_from_durable_jsonl_logs(tmp_path: Path) -> None:
    events_path, usage_path = tmp_path / "events.jsonl", tmp_path / "usage.jsonl"
    event_sink, usage_sink = JsonlEventSink(events_path), JsonlUsageSink(usage_path)
    event_sink.emit(_event("e1", ["a", "b"]))
    usage_sink.emit(_signal("e1", "a", used=True))
    usage_sink.emit(_signal("e1", "b", used=False))

    # read the persisted logs back and compute the metric — the real offline path
    report = utility_at_k(read_event_log(events_path), read_usage_log(usage_path), k=2)
    assert report.utility_at_k == 0.5
    assert (report.n_shown, report.n_used) == (2, 1)


def test_end_to_end_recall_outcome_then_utility() -> None:
    encoder = DeterministicEncoder(dim=64)
    store = InMemoryStore(dim=64)
    for mid, content in (("m_sqlite", "use aiosqlite for the async store"),
                         ("m_style", "prefer terse commit messages")):
        store.add(
            MemoryRecord(MemoryId(mid), Hemisphere.EXPERIENTIAL, "episode", content, SCOPE, NOW),
            encoder.encode([content])[0],
        )
    event_sink, usage_sink = InMemoryEventSink(), InMemoryUsageSink()
    retriever = LoggingRetriever(
        L0Retriever(encoder, store, now=lambda: NOW), event_sink, policy_id="L0", now=lambda: NOW
    )
    gateway = Gateway(retriever, k=2, usage_sink=usage_sink)

    payload = gateway.recall(prompt="how do we do async db work", scope=SCOPE)
    gateway.record_outcome(payload, "import aiosqlite\n# the async store now connects")

    # record_outcome emits only the *citation* signal (a cooperation-dependent self-report). On its
    # own that is not a deterministic captured outcome, so utility@k treats the event as missing
    # data — the deterministic outcome is footprint attribution (usage_attributed.jsonl).
    citation_only = utility_at_k(event_sink.events, usage_sink.signals, k=2)
    assert (citation_only.n_events, citation_only.coverage) == (0, 0.0)  # excluded, not a scored 0

    # Add the deterministic footprint outcome → the event scores: m_sqlite used, m_style not.
    footprints = [
        UsageSignal(e.event_id, MemoryId("m_sqlite"), "footprint", 1.0, True)
        for e in event_sink.events
    ]
    report = utility_at_k(event_sink.events, list(usage_sink.signals) + footprints, k=2)
    assert report.utility_at_k == 0.5
    assert (report.n_events, report.n_shown, report.n_used) == (1, 2, 1)
    assert report.coverage == 1.0
