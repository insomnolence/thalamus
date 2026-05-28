"""The `thalamus dream` cycle wiring: build_dream_scheduler + the context factory
run both passes against a real gateway — the actor refreshes what recall serves,
the proposer records a supersession proposal — without Neo4j or MCP."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.cli.dream import build_dream_scheduler, make_dream_context_factory
from thalamus.core import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.dreaming import InMemoryDreamLog, PassStatus
from thalamus.experiential import InMemorySupersessionIndex
from thalamus.gateway import DerivedViewsRef, Gateway, SupersededDemotingRetriever
from thalamus.retrieval import L0Retriever
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)


def _curated(mid: str, content: str, footprint: tuple[str, ...]) -> MemoryRecord:
    return MemoryRecord(
        MemoryId(mid), Hemisphere.EXPERIENTIAL, "decision", content, SCOPE, NOW,
        metadata={"source": "curated", "footprint": list(footprint)},
    )


def test_one_cycle_refreshes_views_and_records_a_proposal(tmp_path: Path) -> None:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    # `old`/`new`: a supersede lands after composition. `gone`: footprint wholly deleted.
    old = _curated("old", "we use the lexical usage signal", ())
    new = _curated("new", "we use the footprint usage signal", ())
    gone = _curated("gone", "a gotcha about removed.py", ("removed.py",))
    for record in (old, new, gone):
        store.add(record, encoder.encode([record.content])[0])

    index = InMemorySupersessionIndex()
    views = DerivedViewsRef()  # composed empty
    retriever = SupersededDemotingRetriever(
        L0Retriever(encoder, store, now=lambda: NOW), views=views
    )
    gateway = Gateway(retriever, k=5, views=views)

    # Writes that landed AFTER composition (the long-running-serve case).
    index.supersede(old=old.ref, new=new.ref, reason="lexical under-counted", at=NOW)
    # `removed.py` never exists under tmp_path -> gone's whole footprint is missing.

    log = InMemoryDreamLog()
    scheduler = build_dream_scheduler(gateway, dream_log=log)
    context = make_dream_context_factory(
        store=store, supersession=index, scope=SCOPE, repo=tmp_path
    )
    report = scheduler.run(context())

    # Actor (link-resolution) refreshed the served views: the supersede now takes effect.
    assert report.ok
    payload = gateway.recall(prompt="which usage signal", scope=SCOPE)
    assert next(m for m in payload.memories if m.memory_id == "old").superseded is not None

    # Proposer (belief-audit) recorded a propose-only supersession in the dream log.
    audit = next(r for r in log.records if r.report.name == "belief-audit")
    assert audit.report.status is PassStatus.OK
    proposals = audit.report.details["proposals"]
    assert [p["memory_id"] for p in proposals] == ["gone"]

    # Both passes are logged, in order, with their firewall kind.
    assert [(r.report.name, r.report.kind.value) for r in log.records] == [
        ("link-resolution", "actor"),
        ("belief-audit", "proposer"),
    ]
