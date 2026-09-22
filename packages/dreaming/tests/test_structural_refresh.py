"""StructuralRefreshPass re-links episodes that arrive mid-serve to current code modules."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.core import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.dreaming import PassContext, PassStatus, StructuralRefreshPass
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore
from thalamus.structural import (
    InMemoryCrossLinkIndex,
    InMemoryStructuralGraph,
    PythonAstIngestor,
    RelinkQueue,
)
from thalamus.structural.schema import StructuralNode

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


def test_only_links_new_memories_on_later_ticks(tmp_path: Path) -> None:
    # The storm fix: a long-running serve must NOT re-link every episode on every tick.
    _write(tmp_path, "foo.py", "x = 1\n")
    _write(tmp_path, "bar.py", "y = 2\n")
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(PythonAstIngestor().ingest_path(tmp_path, SCOPE))
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    pass_ = StructuralRefreshPass(graph, InMemoryCrossLinkIndex())

    ep1 = MemoryRecord(
        MemoryId("ep1"), Hemisphere.EXPERIENTIAL, "episode", "t", SCOPE, NOW,
        metadata={"footprint": ["foo.py"]},
    )
    store.add(ep1, encoder.encode([ep1.content])[0])
    first = pass_.run(_ctx(store, tmp_path))
    assert first.details["new_memories"] == 1
    assert first.details["links"] == 1

    again = pass_.run(_ctx(store, tmp_path))  # nothing new → re-links nothing (no storm)
    assert again.details["new_memories"] == 0
    assert again.details["links"] == 0

    ep2 = MemoryRecord(
        MemoryId("ep2"), Hemisphere.EXPERIENTIAL, "episode", "t", SCOPE, NOW,
        metadata={"footprint": ["bar.py"]},
    )
    store.add(ep2, encoder.encode([ep2.content])[0])
    third = pass_.run(_ctx(store, tmp_path))  # only the new episode is processed
    assert third.details["new_memories"] == 1


def test_rebuilt_paths_evict_the_linked_cache_so_the_memory_is_relinked(tmp_path: Path) -> None:
    """The regression: a re-derive drops a file's nodes (taking its cross-links on a DETACH
    DELETE backend), and the already-linked cache must not make that loss permanent."""
    _write(tmp_path, "foo.py", "def do_thing():\n    return 1\n")
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(PythonAstIngestor().ingest_path(tmp_path, SCOPE))
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    episode = MemoryRecord(
        MemoryId("ep1"), Hemisphere.EXPERIENTIAL, "episode", "did a thing", SCOPE, NOW,
        metadata={"footprint": ["foo.py"]},
    )
    store.add(episode, encoder.encode([episode.content])[0])

    relink = RelinkQueue()
    pass_ = StructuralRefreshPass(graph, InMemoryCrossLinkIndex(), relink=relink)
    assert pass_.run(_ctx(store, tmp_path)).details["links"] == 1

    # Nothing rebuilt -> the cache holds, no re-link work (the property the cache exists for).
    settled = pass_.run(_ctx(store, tmp_path))
    assert settled.details["new_memories"] == 0
    assert settled.details["repaired"] == 0

    # foo.py was rebuilt: the memory must be re-linked even though it was linked before.
    relink.publish(["foo.py"])
    repaired = pass_.run(_ctx(store, tmp_path))
    assert repaired.details["repaired"] == 1
    assert repaired.details["new_memories"] == 1
    assert repaired.details["links"] == 1


def test_only_memories_touching_a_rebuilt_file_are_evicted(tmp_path: Path) -> None:
    """Eviction is per-footprint, not a cache flush — the cache's CPU win has to survive."""
    _write(tmp_path, "foo.py", "def a() -> int:\n    return 1\n")
    _write(tmp_path, "bar.py", "def b() -> int:\n    return 2\n")
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(PythonAstIngestor().ingest_path(tmp_path, SCOPE))
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    for memory_id, footprint in (("ep-foo", "foo.py"), ("ep-bar", "bar.py")):
        record = MemoryRecord(
            MemoryId(memory_id), Hemisphere.EXPERIENTIAL, "episode", memory_id, SCOPE, NOW,
            metadata={"footprint": [footprint]},
        )
        store.add(record, encoder.encode([record.content])[0])

    relink = RelinkQueue()
    pass_ = StructuralRefreshPass(graph, InMemoryCrossLinkIndex(), relink=relink)
    pass_.run(_ctx(store, tmp_path))

    relink.publish(["foo.py"])
    outcome = pass_.run(_ctx(store, tmp_path))
    assert outcome.details["repaired"] == 1  # ep-bar's cache entry is untouched


def test_line_aware_footprints_are_evicted_by_their_file(tmp_path: Path) -> None:
    """A C-8 ``(file, lines)`` entry is invalidated by the same file being rebuilt."""
    _write(tmp_path, "foo.py", "def a() -> int:\n    return 1\n")
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(PythonAstIngestor().ingest_path(tmp_path, SCOPE))
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    record = MemoryRecord(
        MemoryId("ep"), Hemisphere.EXPERIENTIAL, "episode", "t", SCOPE, NOW,
        metadata={"footprint": ["foo.py"], "footprint_lines": {"foo.py": [1, 2]}},
    )
    store.add(record, encoder.encode([record.content])[0])

    relink = RelinkQueue()
    pass_ = StructuralRefreshPass(graph, InMemoryCrossLinkIndex(), relink=relink)
    pass_.run(_ctx(store, tmp_path))
    relink.publish(["foo.py"])
    assert pass_.run(_ctx(store, tmp_path)).details["repaired"] == 1


def test_without_a_relink_queue_behaviour_is_unchanged(tmp_path: Path) -> None:
    """The repair layer is removable (§14): omit the queue and the pass behaves as before."""
    _write(tmp_path, "foo.py", "x = 1\n")
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(PythonAstIngestor().ingest_path(tmp_path, SCOPE))
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    record = MemoryRecord(
        MemoryId("ep"), Hemisphere.EXPERIENTIAL, "episode", "t", SCOPE, NOW,
        metadata={"footprint": ["foo.py"]},
    )
    store.add(record, encoder.encode([record.content])[0])
    pass_ = StructuralRefreshPass(graph, InMemoryCrossLinkIndex())
    pass_.run(_ctx(store, tmp_path))
    assert pass_.run(_ctx(store, tmp_path)).details["repaired"] == 0


def test_a_settled_cycle_does_not_load_the_code_nodes(tmp_path: Path) -> None:
    """Loading every module and symbol is the pass's real cost (~5s on a large corpus), and a
    settled cycle used to pay it in full to then link nothing."""
    _write(tmp_path, "foo.py", "def do_thing():\n    return 1\n")
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    episode = MemoryRecord(
        MemoryId("ep1"), Hemisphere.EXPERIENTIAL, "episode", "did a thing", SCOPE, NOW,
        metadata={"footprint": ["foo.py"]},
    )
    store.add(episode, encoder.encode([episode.content])[0])

    class _CountingGraph(InMemoryStructuralGraph):
        loads = 0

        def nodes_of_kind(self, scope: Scope, kind: str) -> list[StructuralNode]:
            type(self).loads += 1
            return super().nodes_of_kind(scope, kind)

    graph = _CountingGraph(SCOPE)
    graph.add(PythonAstIngestor().ingest_path(tmp_path, SCOPE))
    pass_ = StructuralRefreshPass(graph, InMemoryCrossLinkIndex())

    pass_.run(_ctx(store, tmp_path))  # links the episode — must load the nodes
    after_first = _CountingGraph.loads
    assert after_first > 0

    outcome = pass_.run(_ctx(store, tmp_path))  # settled: nothing new to link
    assert _CountingGraph.loads == after_first, "a settled cycle must not load the code nodes"
    assert outcome.details["links"] == 0
    assert outcome.details["new_memories"] == 0


def test_the_nodes_are_loaded_again_once_there_is_work(tmp_path: Path) -> None:
    """The skip must not latch: a new memory has to see the current node set."""
    _write(tmp_path, "foo.py", "def do_thing():\n    return 1\n")
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(PythonAstIngestor().ingest_path(tmp_path, SCOPE))
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    links = InMemoryCrossLinkIndex()
    pass_ = StructuralRefreshPass(graph, links)

    pass_.run(_ctx(store, tmp_path))  # empty Brain 1 -> early return
    record = MemoryRecord(
        MemoryId("ep1"), Hemisphere.EXPERIENTIAL, "episode", "t", SCOPE, NOW,
        metadata={"footprint": ["foo.py"]},
    )
    store.add(record, encoder.encode([record.content])[0])

    outcome = pass_.run(_ctx(store, tmp_path))
    assert outcome.details["links"] == 1
    assert [n.node_id for n in links.nodes_for(record.ref)] == ["module:foo"]
