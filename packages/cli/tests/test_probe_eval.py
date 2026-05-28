"""Tests for the probe-eval composition root (pure path)."""

from __future__ import annotations

from datetime import UTC, datetime

from thalamus.cli.probe_eval import compute_probe_eval
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
from thalamus.eval import NullRetriever, TranscriptProbe

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 5, 28, tzinfo=UTC)


def _probe(prompt: str, session: str = "s1") -> TranscriptProbe:
    return TranscriptProbe(prompt=prompt, session_id=session, timestamp=NOW)


def _scored(mid: str, score: float) -> ScoredMemory:
    record = MemoryRecord(MemoryId(mid), Hemisphere.EXPERIENTIAL, "decision", mid, SCOPE, NOW)
    return ScoredMemory(record=record, score=score, features={"relevance": score})


class _Stub:
    def __init__(self, table: dict[str, list[ScoredMemory]]) -> None:
        self._t = table

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        shown = self._t.get(cue.text, [])
        return RetrievalResult(cue=cue, candidates=shown, shown=shown[:k])


def test_compute_probe_eval_runs_the_ablation_and_counts_sessions() -> None:
    probes = [
        _probe("question one", session="s1"),
        _probe("question two", session="s1"),
        _probe("question three", session="s2"),
    ]
    brain_on = _Stub({
        "question one": [_scored("m1", 0.8)],
        "question two": [_scored("m2", 0.4)],
        "question three": [_scored("m3", 0.7)],
    })

    report = compute_probe_eval(
        probes,
        {"brain-off": NullRetriever(), "brain-on": brain_on},
        scope=SCOPE, k=5, threshold=0.5,
    )

    assert report.n_probes == 3
    assert report.n_sessions == 2  # s1 + s2
    assert report.k == 5
    assert report.threshold == 0.5

    # brain-off floor
    assert report.by_retriever["brain-off"].surface_rate == 0.0
    assert report.by_retriever["brain-off"].mean_top_relevance == 0.0

    # brain-on: q1 (0.8) and q3 (0.7) clear 0.5 -> 2/3 surface rate
    on = report.by_retriever["brain-on"]
    assert on.surface_rate == 2 / 3
    # mean over (0.8, 0.4, 0.7)
    assert abs(on.mean_top_relevance - (0.8 + 0.4 + 0.7) / 3) < 1e-9
    # Brain-on strictly above the floor — the lift is positive
    assert on.mean_top_relevance > report.by_retriever["brain-off"].mean_top_relevance
