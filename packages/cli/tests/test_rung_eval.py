"""Tests for the normal-use label/source seams in ``thalamus rung-eval``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.cli.rung_eval import (
    _label_summary,
    _load_logs,
    _plan_delivery_misses,
    _render,
)
from thalamus.core.types import (
    Cue,
    EventId,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    RetrievalResult,
    Scope,
    ScoredMemory,
    TenantId,
)
from thalamus.eval import BenchmarkCase, EvalReport
from thalamus.instrumentation import (
    JsonlEventSink,
    JsonlUsageSink,
    RetrievalEvent,
    ShownItem,
    UsageSignal,
)

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 7, 30, tzinfo=UTC)


def _event(event_id: str, policy_id: str) -> RetrievalEvent:
    return RetrievalEvent(
        event_id=EventId(event_id),
        timestamp=NOW,
        scope=SCOPE,
        policy_id=policy_id,
        cue_text=f"cue-{event_id}",
        k_requested=5,
        candidates=(),
        shown=(ShownItem(MemoryId(f"m-{event_id}"), rank=0, propensity=1.0),),
    )


def _signal(event_id: str, kind: str, *, used: bool) -> UsageSignal:
    return UsageSignal(
        event_id=EventId(event_id),
        memory_id=MemoryId(f"m-{event_id}"),
        kind=kind,
        value=1.0 if used else 0.0,
        used=used,
    )


def test_load_logs_selects_event_population_and_declared_labels(tmp_path: Path) -> None:
    logs = tmp_path / ".thalamus" / "logs"
    JsonlEventSink(logs / "retrieval.jsonl").emit(_event("recall-1", "L0"))
    JsonlEventSink(logs / "plan.jsonl").emit(_event("plan-1", "plan"))
    usage = JsonlUsageSink(logs / "usage.jsonl")
    usage.emit(_signal("recall-1", "declared", used=True))
    usage.emit(_signal("plan-1", "declared", used=False))
    usage.emit(_signal("plan-1", "citation", used=True))

    events, signals = _load_logs(tmp_path, source="plan", label_kind="declared")

    assert [event.event_id for event in events] == [EventId("plan-1")]
    assert [(signal.event_id, signal.kind, signal.used) for signal in signals] == [
        (EventId("plan-1"), "declared", False)
    ]


def test_label_summary_keeps_explicit_none_used_events() -> None:
    events = [_event("used", "L0"), _event("none", "L0"), _event("unlabeled", "L0")]
    signals = [
        _signal("used", "declared", used=True),
        _signal("none", "declared", used=False),
    ]

    summary = _label_summary(events, signals)

    assert summary.labeled_events == 2
    assert summary.used_events == 1
    assert summary.no_use_events == 1


def _scored(memory_id: str) -> ScoredMemory:
    record = MemoryRecord(
        memory_id=MemoryId(memory_id),
        hemisphere=Hemisphere.EXPERIENTIAL,
        kind="decision",
        content=memory_id,
        scope=SCOPE,
        created_at=NOW,
    )
    return ScoredMemory(record=record, score=1.0)


class _FlatRetriever:
    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        shown = [_scored("flat-hit")][:k]
        return RetrievalResult(cue=cue, candidates=shown, shown=shown)


def test_plan_delivery_reports_declared_graph_memory_absent_from_flat_top_k() -> None:
    case = BenchmarkCase(
        cue=Cue("target", SCOPE),
        relevant=frozenset({MemoryId("flat-hit"), MemoryId("graph-only")}),
    )

    assert _plan_delivery_misses(_FlatRetriever(), [case], k=5) == (1, 2)


def test_plan_render_states_one_sided_limit() -> None:
    report = EvalReport(
        k=5,
        n_cases=1,
        recall_at_k=0.5,
        precision_at_k=0.2,
        mrr=0.5,
        hit_rate=1.0,
    )

    rendered = _render(
        {"brain-on": report},
        k=5,
        n_cases=1,
        split=0.2,
        source="plan",
        label_kind="declared",
        plan_delivery=(1, 2),
    )

    assert "one-sided graph delivery = 1/2" in rendered
    assert "not a symmetric or causal win" in rendered
