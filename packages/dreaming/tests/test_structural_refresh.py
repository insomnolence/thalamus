"""StructuralRefreshPass re-links episodes that arrive mid-serve to current code modules."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.core import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.dreaming import PassContext, PassStatus, StructuralRefreshPass
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore
from thalamus.structural import InMemoryCrossLinkIndex, InMemoryStructuralGraph, PythonAstIngestor

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)


def _write(repo: Path, rel: str, src: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(src, encoding="utf-8")


def _ctx(store: InMemoryStore, repo: Path) -> PassContext:
    return PassContext(scope=SCOPE, now=NOW, store=store, repo_root=str(repo))


def test_relinks_a_new_episode_against_current_modules(tmp_path: Path) -> None:
    _write(tmp_path, "foo.py", "def do_thing():\n    return 1\n")
    _write(tmp_path, "bar.py", "def other():\n    return 2\n")
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(PythonAstIngestor().ingest_path(tmp_path, SCOPE))

    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    links = InMemoryCrossLinkIndex()
    pass_ = StructuralRefreshPass(graph, links)

    # Nothing in Brain 1 yet -> nothing to link.
    assert pass_.run(_ctx(store, tmp_path)).details["links"] == 0

    # A new episode arrives mid-serve (the background-sync case), touching foo.py.
    episode = MemoryRecord(
        MemoryId("ep1"), Hemisphere.EXPERIENTIAL, "episode", "did a thing", SCOPE, NOW,
        metadata={"footprint": ["foo.py"]},
    )
    store.add(episode, encoder.encode([episode.content])[0])

    outcome = pass_.run(_ctx(store, tmp_path))
    assert outcome.details["links"] == 1
    # The episode is now linked to module:foo (and not bar) — recallable via cross-links.
    assert [node.node_id for node in links.nodes_for(episode.ref)] == ["module:foo"]


def test_is_idempotent(tmp_path: Path) -> None:
    _write(tmp_path, "foo.py", "x = 1\n")
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(PythonAstIngestor().ingest_path(tmp_path, SCOPE))
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    episode = MemoryRecord(
        MemoryId("ep"), Hemisphere.EXPERIENTIAL, "episode", "t", SCOPE, NOW,
        metadata={"footprint": ["foo.py"]},
    )
    store.add(episode, encoder.encode([episode.content])[0])
    links = InMemoryCrossLinkIndex()
    pass_ = StructuralRefreshPass(graph, links)

    pass_.run(_ctx(store, tmp_path))
    pass_.run(_ctx(store, tmp_path))  # second run must not duplicate the link
    assert [node.node_id for node in links.nodes_for(episode.ref)] == ["module:foo"]


def test_skips_without_store_or_repo_root() -> None:
    graph = InMemoryStructuralGraph(SCOPE)
    outcome = StructuralRefreshPass(graph, InMemoryCrossLinkIndex()).run(
        PassContext(scope=SCOPE, now=NOW)
    )
    assert outcome.status is PassStatus.SKIPPED
