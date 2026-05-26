"""Gateway surfacing of memory staleness (§13.18-D2): a flag, never a deletion."""

from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core.types import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.gateway import Gateway
from thalamus.retrieval import L0Retriever
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 5, 26, tzinfo=UTC)


def _curated(
    store: InMemoryStore, encoder: DeterministicEncoder, mid: str, text: str
) -> MemoryRecord:
    record = MemoryRecord(
        MemoryId(mid), Hemisphere.EXPERIENTIAL, "gotcha", text, SCOPE, NOW,
        metadata={"source": "curated", "footprint": ["worker.py"]},
    )
    store.add(record, encoder.encode([record.content])[0])
    return record


def test_stale_memory_is_flagged() -> None:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    record = _curated(store, encoder, "g1", "the async teardown in worker.py is flaky")
    gateway = Gateway(
        L0Retriever(encoder, store, now=lambda: NOW),
        stale_references={record.ref: ["worker.py"]},
    )
    payload = gateway.recall(prompt="the async teardown in worker.py is flaky", scope=SCOPE)

    item = payload.memories[0]
    assert item.memory_id == MemoryId("g1")
    assert item.stale_references == ("worker.py",)
    rendered = payload.render()
    assert "may be stale" in rendered
    assert "worker.py" in rendered


def test_non_stale_memory_has_no_flag() -> None:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    _curated(store, encoder, "ok", "a fine durable note")
    gateway = Gateway(L0Retriever(encoder, store, now=lambda: NOW))  # no staleness map injected
    payload = gateway.recall(prompt="a fine durable note", scope=SCOPE)

    assert payload.memories[0].stale_references == ()
    assert "may be stale" not in payload.render()
