"""CentralityRefreshPass weights the L-R2 rung from the CYCLE'S Brain 1, not a startup snapshot.

The regression these pin: the pass used to close over the ``store.scan`` taken when the serve was
composed, so a memory written mid-serve never appeared in the weights and its centrality rung read
0 until the next restart. Silent, because an absent memory is indistinguishable from an unlinked
one — the rung simply leaves it on relevance alone.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from thalamus.core import Hemisphere, MemoryId, MemoryRecord, MemoryRef, RepoId, Scope, TenantId
from thalamus.dreaming import CentralityRefreshPass, PassContext, PassStatus
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 9, 22, tzinfo=UTC)


def _record(memory_id: str) -> MemoryRecord:
    return MemoryRecord(
        MemoryId(memory_id), Hemisphere.EXPERIENTIAL, "episode", memory_id, SCOPE, NOW,
        metadata={"footprint": ["foo.py"]},
    )


def _store(*ids: str) -> InMemoryStore:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    for memory_id in ids:
        record = _record(memory_id)
        store.add(record, encoder.encode([record.content])[0])
    return store


def _ctx(store: InMemoryStore | None) -> PassContext:
    return PassContext(scope=SCOPE, now=NOW, store=store)


def test_weights_cover_memories_written_after_the_serve_started() -> None:
    """The fix: a memory added mid-serve must get a weight on the very next cycle."""
    store = _store("ep1")
    seen: list[Sequence[MemoryRef]] = []

    def recompute(memories: Sequence[MemoryRef]) -> Mapping[MemoryRef, float]:
        seen.append(list(memories))
        return {ref: 1.0 for ref in memories}

    published: list[Mapping[MemoryRef, float]] = []
    pass_ = CentralityRefreshPass(recompute, published.append)

    pass_.run(_ctx(store))
    assert [str(r.memory_id) for r in seen[-1]] == ["ep1"]

    encoder = DeterministicEncoder(dim=32)
    new = _record("ep2")
    store.add(new, encoder.encode([new.content])[0])

    pass_.run(_ctx(store))
    assert sorted(str(r.memory_id) for r in seen[-1]) == ["ep1", "ep2"]
    assert new.ref in published[-1], "a mid-serve memory must be weighted, not silently zero"


def test_it_reads_the_cycle_snapshot_rather_than_rescanning() -> None:
    """It must go through PassContext.memories so the cycle's single Brain-1 read is reused."""
    store = _store("ep1")
    ctx = _ctx(store)
    ctx.memories()  # cycle already read Brain 1

    encoder = DeterministicEncoder(dim=32)
    late = _record("ep-late")
    store.add(late, encoder.encode([late.content])[0])  # lands after the cycle's read

    seen: list[Sequence[MemoryRef]] = []
    CentralityRefreshPass(
        lambda m: (seen.append(list(m)), {})[1], lambda w: None
    ).run(ctx)
    assert [str(r.memory_id) for r in seen[0]] == ["ep1"], "should use the cycle's snapshot"


def test_no_store_skips_rather_than_publishing_empty_weights() -> None:
    """Publishing {} would wipe the rung; absent Brain 1 means 'unknown', not 'nothing links'."""
    published: list[Mapping[MemoryRef, float]] = []
    outcome = CentralityRefreshPass(lambda m: {}, published.append).run(_ctx(None))
    assert outcome.status is PassStatus.SKIPPED
    assert published == []
