from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.types import (
    Cue,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    TenantId,
)
from thalamus.eval import (
    BenchmarkCase,
    NullRetriever,
    compare,
    evaluate,
    hit_at_k,
    load_cases,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from thalamus.retrieval import L0Retriever
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))
NOW = datetime(2026, 5, 25, tzinfo=UTC)


def _retriever() -> L0Retriever:
    encoder = DeterministicEncoder(dim=128)
    store = InMemoryStore(dim=128)
    docs = {"a": "alpha apple orchard", "b": "beta banana bread", "c": "gamma grape vine"}
    for mid, text in docs.items():
        store.add(
            MemoryRecord(MemoryId(mid), Hemisphere.EXPERIENTIAL, "episode", text, SCOPE, NOW),
            encoder.encode([text])[0],
        )
    return L0Retriever(encoder, store, now=lambda: NOW)


CASES = [
    BenchmarkCase(Cue(text="alpha apple orchard", scope=SCOPE), frozenset({MemoryId("a")})),
    BenchmarkCase(Cue(text="beta banana bread", scope=SCOPE), frozenset({MemoryId("b")})),
]


def test_metrics() -> None:
    shown = [MemoryId("x"), MemoryId("y"), MemoryId("z")]
    relevant = {MemoryId("y")}
    assert recall_at_k(shown, relevant, 3) == 1.0
    assert recall_at_k(shown, relevant, 1) == 0.0
    assert reciprocal_rank(shown, relevant) == 0.5
    assert hit_at_k(shown, relevant, 2) == 1.0
    assert precision_at_k(shown, relevant, 2) == 0.5


def test_evaluate_l0_recovers_relevant() -> None:
    report = evaluate(_retriever(), CASES, k=1)
    assert report.n_cases == 2
    assert report.recall_at_k == 1.0
    assert report.mrr == 1.0
    assert report.hit_rate == 1.0


def test_compare_brain_off_is_the_floor() -> None:
    reports = compare({"L0": _retriever(), "brain-off": NullRetriever()}, CASES, k=3)
    assert reports["brain-off"].recall_at_k == 0.0
    assert reports["L0"].recall_at_k > reports["brain-off"].recall_at_k


def test_load_cases(tmp_path: Path) -> None:
    path = tmp_path / "bench.jsonl"
    content = '{"query":"alpha apple","relevant":["a"]}\n{"query":"beta","relevant":["b"]}\n'
    path.write_text(content, encoding="utf-8")
    cases = load_cases(path, SCOPE)
    assert len(cases) == 2
    assert cases[0].relevant == frozenset({MemoryId("a")})
    assert cases[0].cue.text == "alpha apple"
