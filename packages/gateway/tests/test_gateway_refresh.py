"""The DerivedViews refresh seam: a single swap reaches both the demoting
retriever's promotion and the gateway's annotation, recalls snapshot once, and
the legacy (non-refreshing) construction path is unchanged."""

from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core.types import (
    Cue,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    MemoryRef,
    RepoId,
    RetrievalResult,
    Scope,
    ScoredMemory,
    Supersession,
    TenantId,
)
from thalamus.gateway import (
    DerivedViews,
    DerivedViewsRef,
    Gateway,
    SupersededDemotingRetriever,
)

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)


def _record(mid: str, content: str) -> MemoryRecord:
    return MemoryRecord(
        MemoryId(mid), Hemisphere.EXPERIENTIAL, "decision", content, SCOPE, NOW,
        metadata={"source": "curated"},
    )


def _ref(mid: str) -> MemoryRef:
    return MemoryRef(scope=SCOPE, memory_id=MemoryId(mid))


def _superseded_by(new: str) -> Supersession:
    return Supersession(MemoryId(new), "replaced", NOW)


class _StubRetriever:
    """Returns a fixed candidate set in a fixed (highest-score-first) order, every call."""

    def __init__(self, records: list[MemoryRecord]) -> None:
        self._records = records

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        candidates = [
            ScoredMemory(record=r, score=1.0 - i * 0.01) for i, r in enumerate(self._records)
        ]
        return RetrievalResult(cue=cue, candidates=candidates, shown=candidates[: max(k, 0)])


def test_refresh_reaches_both_demotion_and_annotation_through_a_shared_ref() -> None:
    old, new = _record("old", "the old belief"), _record("new", "the current belief")
    views_ref = DerivedViewsRef()  # empty: nothing superseded, nothing stale
    retriever = SupersededDemotingRetriever(_StubRetriever([old, new]), views=views_ref)
    gateway = Gateway(retriever, k=2, views=views_ref)

    # Before refresh: old ranks first (higher score), not annotated.
    before = gateway.recall(prompt="q", scope=SCOPE)
    assert [m.memory_id for m in before.memories] == ["old", "new"]
    assert before.memories[0].superseded is None

    # Refresh marks ``old`` superseded by ``new`` — one swap.
    gateway.refresh(DerivedViews(superseded={_ref("old"): _superseded_by("new")}))

    after = gateway.recall(prompt="q", scope=SCOPE)
    # Demotion: current belief now outranks the superseded one for the shown slots.
    assert [m.memory_id for m in after.memories] == ["new", "old"]
    # Annotation: the superseded item carries its supersession note.
    superseded_item = next(m for m in after.memories if m.memory_id == "old")
    assert superseded_item.superseded is not None
    assert superseded_item.superseded.reason == "replaced"


def test_refresh_updates_stale_references() -> None:
    rec = _record("m", "a gotcha about worker.py")
    views_ref = DerivedViewsRef()
    gateway = Gateway(_StubRetriever([rec]), k=1, views=views_ref)

    assert gateway.recall(prompt="q", scope=SCOPE).memories[0].stale_references == ()

    gateway.refresh(DerivedViews(stale_references={_ref("m"): ("worker.py",)}))

    assert gateway.recall(prompt="q", scope=SCOPE).memories[0].stale_references == ("worker.py",)


def test_recall_snapshots_the_views_ref_once_per_call() -> None:
    class _CountingRef(DerivedViewsRef):
        def __init__(self, views: DerivedViews) -> None:
            self._v = views
            self.reads = 0

        @property  # type: ignore[override]
        def views(self) -> DerivedViews:
            self.reads += 1
            return self._v

    ref = _CountingRef(DerivedViews(stale_references={_ref("a"): ("x.py",)}))
    gateway = Gateway(_StubRetriever([_record("a", "t1"), _record("b", "t2")]), k=2, views=ref)

    gateway.recall(prompt="q", scope=SCOPE)

    # Read exactly once despite iterating multiple shown candidates — no torn read window.
    assert ref.reads == 1


def test_legacy_superseded_kwarg_still_works_without_refresh() -> None:
    old, new = _record("old", "old"), _record("new", "new")
    superseded = {_ref("old"): _superseded_by("new")}
    # No shared ref, no refresh — the back-compat path builds a private snapshot from the kwarg.
    retriever = SupersededDemotingRetriever(_StubRetriever([old, new]), superseded)
    gateway = Gateway(retriever, k=2, superseded=superseded)

    payload = gateway.recall(prompt="q", scope=SCOPE)
    assert [m.memory_id for m in payload.memories] == ["new", "old"]
    assert next(m for m in payload.memories if m.memory_id == "old").superseded is not None
