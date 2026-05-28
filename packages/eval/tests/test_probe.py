"""Probe corpus eval — surface-rate + top-score distribution + ablation switch."""

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
from thalamus.eval import NullRetriever, compare_probes, evaluate_probes
from thalamus.eval.transcripts import TranscriptProbe

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 5, 28, tzinfo=UTC)


def _probe(prompt: str) -> TranscriptProbe:
    return TranscriptProbe(prompt=prompt, session_id="s", timestamp=NOW)


def _scored(mid: str, score: float) -> ScoredMemory:
    record = MemoryRecord(MemoryId(mid), Hemisphere.EXPERIENTIAL, "decision", mid, SCOPE, NOW)
    return ScoredMemory(record=record, score=score, features={"relevance": score})


class _FakeRetriever:
    """Returns the given (prompt -> ScoredMemory list) mapping; otherwise empty."""

    def __init__(self, table: dict[str, list[ScoredMemory]]) -> None:
        self._table = table

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        shown = self._table.get(cue.text, [])
        return RetrievalResult(cue=cue, candidates=shown, shown=shown[:k])


def test_evaluate_probes_summarises_surface_quality() -> None:
    probes = [_probe("a"), _probe("b"), _probe("c")]
    retriever = _FakeRetriever({
        "a": [_scored("m1", 0.9), _scored("m2", 0.4)],
        "b": [_scored("m3", 0.55)],
        # "c" -> nothing surfaced
    })

    report, outcomes = evaluate_probes(
        retriever, probes, scope=SCOPE, k=5, threshold=0.5, label="L0"
    )

    assert report.label == "L0"
    assert report.n_probes == 3
    # surface_rate counts probes whose top-1 cleared the threshold (a, b) over n (3)
    assert report.surface_rate == 2 / 3
    # mean_top_relevance over (0.9, 0.55, 0.0)
    assert abs(report.mean_top_relevance - (0.9 + 0.55 + 0.0) / 3) < 1e-9
    assert report.median_top_relevance == 0.55
    # Per-probe outcomes carry the surfaced ids + top score
    assert [o.shown for o in outcomes] == [
        (MemoryId("m1"), MemoryId("m2")), (MemoryId("m3"),), (),
    ]
    assert outcomes[2].top_relevance == 0.0


def test_brain_off_floor_is_zero_surface_rate() -> None:
    probes = [_probe(p) for p in ("a", "b")]
    report, _ = evaluate_probes(
        NullRetriever(), probes, scope=SCOPE, k=5, threshold=0.0, label="brain-off"
    )
    assert report.surface_rate == 0.0
    assert report.mean_top_relevance == 0.0
    assert report.median_top_relevance == 0.0


def test_compare_probes_runs_the_ablation_switch() -> None:
    probes = [_probe("a"), _probe("b")]
    brain_on = _FakeRetriever({"a": [_scored("m1", 0.8)], "b": [_scored("m2", 0.6)]})
    reports = compare_probes(
        {"brain-off": NullRetriever(), "L0": brain_on},
        probes, scope=SCOPE, k=5, threshold=0.5,
    )
    assert set(reports) == {"brain-off", "L0"}
    assert reports["brain-off"].surface_rate == 0.0
    assert reports["L0"].surface_rate == 1.0
    assert reports["L0"].mean_top_relevance > reports["brain-off"].mean_top_relevance


def test_empty_corpus_returns_zeroed_report() -> None:
    report, outcomes = evaluate_probes(
        _FakeRetriever({}), [], scope=SCOPE, k=5, threshold=0.0, label="x"
    )
    assert report.n_probes == 0
    assert report.surface_rate == 0.0
    assert outcomes == []


def test_threshold_separates_confident_from_weak_hits() -> None:
    probes = [_probe("strong"), _probe("weak")]
    retriever = _FakeRetriever({
        "strong": [_scored("m1", 0.9)],
        "weak": [_scored("m2", 0.3)],
    })
    strict, _ = evaluate_probes(
        retriever, probes, scope=SCOPE, k=5, threshold=0.5, label="strict"
    )
    permissive, _ = evaluate_probes(
        retriever, probes, scope=SCOPE, k=5, threshold=0.1, label="permissive"
    )
    assert strict.surface_rate == 0.5  # only "strong" clears 0.5
    assert permissive.surface_rate == 1.0  # both clear 0.1
