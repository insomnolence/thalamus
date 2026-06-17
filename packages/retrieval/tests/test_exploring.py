"""Tests for ExploringRetriever (R-7) — calibrated exploration with an exact logged propensity."""

from __future__ import annotations

import random
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
from thalamus.instrumentation import InMemoryEventSink, LoggingRetriever
from thalamus.retrieval import ExploringRetriever, explore_selection
from thalamus.retrieval.exploring import PROPENSITY_FEATURE

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))
NOW = datetime(2026, 6, 17, tzinfo=UTC)
CUE = Cue(text="q", scope=SCOPE)


def _scored(mid: str, score: float) -> ScoredMemory:
    record = MemoryRecord(
        memory_id=MemoryId(mid), hemisphere=Hemisphere.EXPERIENTIAL, kind="episode",
        content=mid, scope=SCOPE, created_at=NOW,
    )
    return ScoredMemory(record=record, score=score, features={"relevance": score})


def _pool(n: int) -> list[ScoredMemory]:
    return [_scored(f"m{i}", 1.0 - i * 0.01) for i in range(n)]


class _Stub:
    def __init__(self, ordered: list[ScoredMemory]) -> None:
        self._ordered = ordered

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        return RetrievalResult(cue=cue, candidates=self._ordered, shown=self._ordered[:k])


def test_epsilon_zero_is_deterministic_top_k_with_unit_propensity() -> None:
    shown, prop = explore_selection(_pool(10), 3, epsilon=0.0, pool=8, rng=random.Random(0))
    assert [str(m.record.memory_id) for m in shown] == ["m0", "m1", "m2"]
    assert prop == {MemoryId("m0"): 1.0, MemoryId("m1"): 1.0, MemoryId("m2"): 1.0}


def test_marginal_propensities_are_exact() -> None:
    # k=2, pool=5, eps=0.5 → top-2 marginal = (1-eps)+eps*(k/pool); others-in-pool = eps*(k/pool)
    eps, k, pool = 0.5, 2, 5
    explore_marginal = eps * (k / pool)  # 0.2
    # the realized propensity is a property of the item, not the draw
    seen: dict[str, float] = {}
    for seed in range(200):
        _, prop = explore_selection(_pool(10), k, epsilon=eps, pool=pool, rng=random.Random(seed))
        shown = [_scored(m, 0) for m in prop]
        for m in shown:
            seen[str(m.record.memory_id)] = prop[m.record.memory_id]  # same item → same propensity
    assert seen["m0"] == (1.0 - eps) + explore_marginal  # 0.7, a top-k item
    assert seen["m1"] == (1.0 - eps) + explore_marginal  # 0.7, a top-k item
    assert abs(seen["m3"] - explore_marginal) < 1e-9      # 0.2, in-pool not top-k
    assert "m9" not in seen  # below the pool → never shown


def test_exploration_reaches_below_top_k_with_common_support() -> None:
    # With eps>0, items outside the top-k must sometimes be shown (the whole point: common support).
    rng = random.Random(1)
    below_top_k_shown = False
    for _ in range(100):
        shown, _ = explore_selection(_pool(10), 2, epsilon=0.5, pool=6, rng=rng)
        if any(str(m.record.memory_id) not in {"m0", "m1"} for m in shown):
            below_top_k_shown = True
            break
    assert below_top_k_shown


def test_retriever_stamps_propensity_into_shown_features() -> None:
    inner = _Stub(_pool(10))
    explorer = ExploringRetriever(inner, epsilon=0.5, pool=5, rng=random.Random(3))
    result = explorer.retrieve(CUE, 2)
    assert len(result.shown) == 2
    for item in result.shown:
        assert PROPENSITY_FEATURE in item.features
        assert 0.0 < item.features[PROPENSITY_FEATURE] <= 1.0
    assert result.candidates == inner.retrieve(CUE, 2).candidates  # ranking untouched


def test_fewer_candidates_than_k_serves_all_with_unit_propensity() -> None:
    shown, prop = explore_selection(_pool(2), 5, epsilon=0.5, pool=8, rng=random.Random(0))
    assert len(shown) == 2
    assert all(p == 1.0 for p in prop.values())  # no room to explore → deterministic


def test_explored_propensity_reaches_the_retrieval_log() -> None:
    # End-to-end: ExploringRetriever stamps it, LoggingRetriever records it — the irreversible bit.
    sink = InMemoryEventSink()
    explorer = ExploringRetriever(_Stub(_pool(10)), epsilon=1.0, pool=4, rng=random.Random(7))
    logged = LoggingRetriever(explorer, sink, policy_id="p+explore")
    logged.retrieve(CUE, 2)
    (event,) = sink.events
    propensities = [s.propensity for s in event.shown]
    assert len(propensities) == 2
    assert all(0.0 < p < 1.0 for p in propensities)  # sub-1 ⇒ off-policy eval has common support
