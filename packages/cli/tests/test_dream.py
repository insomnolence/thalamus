"""The `thalamus dream` cycle wiring: build_dream_scheduler + the context factory
run both passes against a real gateway — the actor refreshes what recall serves,
the proposer records a supersession proposal — without Neo4j or MCP."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.cli.dream import (
    _convergence_probes,
    build_dream_scheduler,
    make_dream_context_factory,
)
from thalamus.core import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.dreaming import (
    InMemoryDreamLog,
    PassContext,
    PassKind,
    PassOutcome,
    PassStatus,
    StructuralRederivePass,
    check_convergence,
    snapshot,
)
from thalamus.experiential import InMemorySupersessionIndex
from thalamus.gateway import DerivedViewsRef, Gateway, SupersededDemotingRetriever
from thalamus.retrieval import L0Retriever
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore
from thalamus.structural import (
    CorpusSpec,
    InMemoryCrossLinkIndex,
    InMemoryFileManifest,
    InMemoryStructuralGraph,
    InMemoryStructuralIndex,
    PythonAstIngestor,
    python_files,
)

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

    # Both passes are logged, in order, with their firewall kind. No structural-refresh here:
    # this gateway has no Brain 2 (graph/links), so the pass is conditionally absent.
    assert [(r.report.name, r.report.kind.value) for r in log.records] == [
        ("link-resolution", "actor"),
        ("belief-audit", "proposer"),
    ]


def test_scheduler_includes_structural_refresh_when_brain2_present(tmp_path: Path) -> None:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    views = DerivedViewsRef()
    retriever = SupersededDemotingRetriever(
        L0Retriever(encoder, store, now=lambda: NOW), views=views
    )
    # A gateway WITH a structural graph + link index -> structural-refresh is wired in, first.
    gateway = Gateway(
        retriever, k=5, views=views,
        graph=InMemoryStructuralGraph(SCOPE), links=InMemoryCrossLinkIndex(),
    )
    log = InMemoryDreamLog()
    scheduler = build_dream_scheduler(gateway, dream_log=log)
    context = make_dream_context_factory(
        store=store, supersession=InMemorySupersessionIndex(), scope=SCOPE, repo=tmp_path
    )
    scheduler.run(context())

    assert [r.report.name for r in log.records] == [
        "structural-refresh",
        "link-resolution",
        "belief-audit",
    ]


def test_scheduler_shares_the_rederive_queue_with_the_relink(tmp_path: Path) -> None:
    """The composition-root wiring: a re-derive's rebuilt paths must reach the re-link pass.

    Unit tests cover each pass in isolation; this pins the seam between them, which is where the
    repair would silently regress (the passes would both still pass their own tests).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    episode = _curated("ep", "why mod.py is like that", ("mod.py",))
    store.add(episode, encoder.encode([episode.content])[0])

    graph = InMemoryStructuralGraph(SCOPE)
    links = InMemoryCrossLinkIndex()
    views = DerivedViewsRef()
    gateway = Gateway(
        L0Retriever(encoder, store, now=lambda: NOW), k=5, views=views,
        graph=graph, links=links,
    )
    rederive = StructuralRederivePass(
        [CorpusSpec(PythonAstIngestor(), InMemoryStructuralIndex(dim=32), python_files, "code")],
        graph, InMemoryFileManifest(), encoder,
    )
    # No `relink` argument: the scheduler derives it from the re-derive, so the repair cannot be
    # switched off by a caller forgetting to connect them.
    scheduler = build_dream_scheduler(gateway, structural_rederive=rederive)
    context = make_dream_context_factory(
        store=store, supersession=InMemorySupersessionIndex(), scope=SCOPE, repo=repo
    )

    scheduler.run(context())  # first cycle: build Brain 2 and link the episode
    assert [node.node_id for node in links.nodes_for(episode.ref)] == ["module:mod"]

    # A changed file rebuilds its nodes; the re-link must be told, and repair in the same cycle.
    (repo / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    report = scheduler.run(context())

    refresh = next(p for p in report.passes if p.name == "structural-refresh")
    assert refresh.details["repaired"] == 1


def test_convergence_probes_read_live_state(tmp_path: Path) -> None:
    """The probes must re-read on each call — one that captured a value at construction would
    report convergence no matter what the passes did."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    record = _curated("ep", "why mod.py is like that", ("mod.py",))
    store.add(record, encoder.encode([record.content])[0])

    graph = InMemoryStructuralGraph(SCOPE)
    links = InMemoryCrossLinkIndex()
    gateway = Gateway(
        L0Retriever(encoder, store, now=lambda: NOW), k=5, views=DerivedViewsRef(),
        graph=graph, links=links,
    )
    probes = _convergence_probes(gateway, store, SCOPE)
    assert set(probes) == {
        "views.superseded", "views.stale_references", "brain2.nodes", "crosslinks"
    }

    before = snapshot(probes)
    graph.add(PythonAstIngestor().ingest_path(repo, SCOPE))
    after = snapshot(probes)
    assert "brain2.nodes" in before.differences(after)


def test_convergence_over_the_real_pass_set_is_stable(tmp_path: Path) -> None:
    """End-to-end: the assembled cycle must leave its own derived views alone on a repeat run.
    This is the precondition every future pass gate depends on."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    record = _curated("ep", "why mod.py is like that", ("mod.py",))
    store.add(record, encoder.encode([record.content])[0])

    graph = InMemoryStructuralGraph(SCOPE)
    links = InMemoryCrossLinkIndex()
    gateway = Gateway(
        L0Retriever(encoder, store, now=lambda: NOW), k=5, views=DerivedViewsRef(),
        graph=graph, links=links,
    )
    rederive = StructuralRederivePass(
        [CorpusSpec(PythonAstIngestor(), InMemoryStructuralIndex(dim=32), python_files, "code")],
        graph, InMemoryFileManifest(), encoder,
    )
    scheduler = build_dream_scheduler(gateway, structural_rederive=rederive)
    context = make_dream_context_factory(
        store=store, supersession=InMemorySupersessionIndex(), scope=SCOPE, repo=repo
    )

    report = check_convergence(
        scheduler, context, _convergence_probes(gateway, store, SCOPE), rounds=3
    )
    assert report.converged, report.render()


def test_the_centrality_gate_releases_when_brain2_actually_changes(tmp_path: Path) -> None:
    """The half of a gate that is easy to get wrong: it must stop skipping once its inputs move.

    Centrality feeds recall, so a gate that latched shut would quietly freeze the rung at its
    startup value — the same silent-staleness shape as the cross-hemisphere link bug.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    record = _curated("ep", "why mod.py is like that", ("mod.py",))
    store.add(record, encoder.encode([record.content])[0])

    graph = InMemoryStructuralGraph(SCOPE)
    links = InMemoryCrossLinkIndex()
    gateway = Gateway(
        L0Retriever(encoder, store, now=lambda: NOW), k=5, views=DerivedViewsRef(),
        graph=graph, links=links,
    )
    manifest = InMemoryFileManifest()
    rederive = StructuralRederivePass(
        [CorpusSpec(PythonAstIngestor(), InMemoryStructuralIndex(dim=32), python_files, "code")],
        graph, manifest, encoder,
    )
    runs: list[int] = []

    class _CountingCentrality:
        name = "centrality-refresh"
        kind = PassKind.ACTOR

        def run(self, ctx: PassContext) -> PassOutcome:
            runs.append(1)
            return PassOutcome(summary="recomputed")

    scheduler = build_dream_scheduler(
        gateway,
        structural_rederive=rederive,
        centrality_refresh=_CountingCentrality(),
        manifest=manifest,
        scope=SCOPE,
    )
    context = make_dream_context_factory(
        store=store, supersession=InMemorySupersessionIndex(), scope=SCOPE, repo=repo
    )

    scheduler.run(context())          # cold: builds Brain 2, links, recomputes
    scheduler.run(context())          # settled: must skip
    assert len(runs) == 1, "a quiet cycle should not recompute centrality"

    (repo / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    report = scheduler.run(context())  # Brain 2 rebuilt -> the gate must release

    centrality = next(p for p in report.passes if p.name == "centrality-refresh")
    assert centrality.status is PassStatus.OK, "the gate latched shut on a real change"
    assert len(runs) == 2
