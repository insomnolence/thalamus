"""LinkResolutionPass (actor) fixes the frozen-derived-views bug a long-running
serve suffers: a supersede that lands after composition, and code deleted after
start-up, only take effect once the pass re-reads durable truth and refreshes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.core import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.dreaming import LinkResolutionPass, PassContext
from thalamus.experiential import InMemorySupersessionIndex
from thalamus.gateway import DerivedViewsRef, Gateway, SupersededDemotingRetriever
from thalamus.retrieval import L0Retriever
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)


def _curated(mid: str, content: str, footprint: tuple[str, ...] = ()) -> MemoryRecord:
    return MemoryRecord(
        MemoryId(mid), Hemisphere.EXPERIENTIAL, "decision", content, SCOPE, NOW,
        metadata={"source": "curated", "footprint": list(footprint)},
    )


def _ctx(
    store: InMemoryStore, index: InMemorySupersessionIndex, repo_root: str | None
) -> PassContext:
    return PassContext(
        scope=SCOPE, now=NOW, store=store, supersession=index, repo_root=repo_root
    )


def _build_gateway(
    encoder: DeterministicEncoder, store: InMemoryStore, views: DerivedViewsRef
) -> Gateway:
    base = L0Retriever(encoder, store, now=lambda: NOW)
    retriever = SupersededDemotingRetriever(base, views=views)
    return Gateway(retriever, k=5, views=views)


def test_supersede_after_composition_takes_effect_only_after_the_pass_runs() -> None:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    old = _curated("old", "we use the lexical overlap usage signal")
    new = _curated("new", "we use the footprint overlap usage signal")
    for record in (old, new):
        store.add(record, encoder.encode([record.content])[0])

    index = InMemorySupersessionIndex()
    views = DerivedViewsRef()  # composed empty — nothing superseded yet
    gateway = _build_gateway(encoder, store, views)

    # A supersede lands AFTER composition (the live `remember --supersedes` case).
    index.supersede(old=old.ref, new=new.ref, reason="lexical under-counted real usage", at=NOW)

    # FROZEN BUG: recall still has no idea the old belief was superseded.
    before = gateway.recall(prompt="which usage signal", scope=SCOPE)
    assert all(m.superseded is None for m in before.memories)

    # The actor pass re-reads durable truth and refreshes.
    outcome = LinkResolutionPass(gateway.refresh).run(_ctx(store, index, repo_root=None))
    assert outcome.details["superseded"] == 1

    # FIXED: the superseded belief is now annotated and demoted below current truth.
    after = gateway.recall(prompt="which usage signal", scope=SCOPE)
    superseded = next(m for m in after.memories if m.memory_id == "old")
    assert superseded.superseded is not None
    assert superseded.superseded.reason == "lexical under-counted real usage"


def test_staleness_is_recomputed_from_disk(tmp_path: Path) -> None:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    (tmp_path / "live.py").write_text("x = 1\n")
    rec = _curated("m", "a gotcha about live.py", footprint=("live.py",))
    store.add(rec, encoder.encode([rec.content])[0])

    index = InMemorySupersessionIndex()
    views = DerivedViewsRef()
    gateway = _build_gateway(encoder, store, views)
    pass_ = LinkResolutionPass(gateway.refresh)

    # File present -> not stale.
    pass_.run(_ctx(store, index, repo_root=str(tmp_path)))
    assert gateway.recall(prompt="gotcha", scope=SCOPE).memories[0].stale_references == ()

    # Delete it -> the next cycle flags the belief about it.
    (tmp_path / "live.py").unlink()
    outcome = pass_.run(_ctx(store, index, repo_root=str(tmp_path)))
    assert outcome.details["stale"] == 1
    assert gateway.recall(prompt="gotcha", scope=SCOPE).memories[0].stale_references == ("live.py",)


def test_skips_without_a_store_or_supersession_handle() -> None:
    pass_ = LinkResolutionPass(lambda views: None)
    outcome = pass_.run(PassContext(scope=SCOPE, now=NOW))
    assert outcome.summary == "no store/supersession handle wired"
