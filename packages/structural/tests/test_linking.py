from __future__ import annotations

from pathlib import Path

from thalamus.core.types import MemoryId, MemoryRef, RepoId, Scope, TenantId
from thalamus.structural import InMemoryCrossLinkIndex, PythonAstIngestor, link_by_footprint

SCOPE = Scope(TenantId("t"), RepoId("r"))


def _write(repo: Path, rel: str, src: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(src, encoding="utf-8")


def test_links_episode_to_touched_module(tmp_path: Path) -> None:
    _write(tmp_path, "foo.py", "def do_thing():\n    return 1\n")
    _write(tmp_path, "bar.py", "def other():\n    return 2\n")
    result = PythonAstIngestor().ingest_path(tmp_path, SCOPE)
    links = InMemoryCrossLinkIndex()

    created = link_by_footprint(
        [(MemoryRef(SCOPE, MemoryId("ep1")), ["foo.py"])], result.nodes, links, repo_root=tmp_path
    )
    assert created == 1
    nodes = links.nodes_for(MemoryRef(SCOPE, MemoryId("ep1")))
    assert [node.node_id for node in nodes] == ["module:foo"]  # touched foo, not bar
    assert MemoryRef(SCOPE, MemoryId("ep1")) in links.memories_for(nodes[0])


def test_absolute_anchor_matches_relative_footprint(tmp_path: Path) -> None:
    # anchors are absolute (ingested from an absolute root); footprints are repo-relative
    _write(tmp_path, "sub/mod.py", "def f():\n    return 0\n")
    result = PythonAstIngestor().ingest_path(tmp_path, SCOPE)
    module = next(node for node in result.nodes if node.kind == "module")
    assert module.anchor is not None and Path(module.anchor.path).is_absolute()

    links = InMemoryCrossLinkIndex()
    created = link_by_footprint(
        [(MemoryRef(SCOPE, MemoryId("e")), ["sub/mod.py"])], result.nodes, links, repo_root=tmp_path
    )
    assert created == 1


def test_non_python_or_unmatched_files_do_not_link(tmp_path: Path) -> None:
    _write(tmp_path, "foo.py", "x = 1\n")
    result = PythonAstIngestor().ingest_path(tmp_path, SCOPE)
    links = InMemoryCrossLinkIndex()

    created = link_by_footprint(
        [(MemoryRef(SCOPE, MemoryId("ep")), ["README.md", "missing.py"])],
        result.nodes,
        links,
        repo_root=tmp_path,
    )
    assert created == 0  # links are never forced
    assert links.nodes_for(MemoryRef(SCOPE, MemoryId("ep"))) == []


def test_idempotent_relink(tmp_path: Path) -> None:
    _write(tmp_path, "foo.py", "y = 1\n")
    result = PythonAstIngestor().ingest_path(tmp_path, SCOPE)
    links = InMemoryCrossLinkIndex()
    items = [(MemoryRef(SCOPE, MemoryId("ep")), ["foo.py"])]

    link_by_footprint(items, result.nodes, links, repo_root=tmp_path)
    link_by_footprint(items, result.nodes, links, repo_root=tmp_path)  # re-run
    assert len(links.nodes_for(MemoryRef(SCOPE, MemoryId("ep")))) == 1


# ── C-7: symbol-level linking when the footprint carries line info, module fallback otherwise ──

_SYMBOLS_SRC = """\
def alpha():
    return 1


def beta():
    return 2
"""


def test_line_aware_footprint_links_to_enclosing_symbol(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", _SYMBOLS_SRC)
    result = PythonAstIngestor().ingest_path(tmp_path, SCOPE)
    links = InMemoryCrossLinkIndex()

    # The diff touched line 2 (inside alpha) — link to the symbol, not the module.
    created = link_by_footprint(
        [(MemoryRef(SCOPE, MemoryId("ep")), [("m.py", [2])])],
        result.nodes,
        links,
        repo_root=tmp_path,
    )
    assert created == 1
    ids = [n.node_id for n in links.nodes_for(MemoryRef(SCOPE, MemoryId("ep")))]
    assert ids == ["function:m.alpha"]


def test_line_aware_footprint_links_each_touched_symbol(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", _SYMBOLS_SRC)
    result = PythonAstIngestor().ingest_path(tmp_path, SCOPE)
    links = InMemoryCrossLinkIndex()

    # Lines in both functions → both symbol nodes, deduped (line 2 twice → one link).
    created = link_by_footprint(
        [(MemoryRef(SCOPE, MemoryId("ep")), [("m.py", [2, 2, 6])])],
        result.nodes,
        links,
        repo_root=tmp_path,
    )
    assert created == 2
    ids = {n.node_id for n in links.nodes_for(MemoryRef(SCOPE, MemoryId("ep")))}
    assert ids == {"function:m.alpha", "function:m.beta"}


def test_line_outside_any_symbol_falls_back_to_module(tmp_path: Path) -> None:
    # A blank line between the two functions is enclosed by no symbol → module fallback.
    _write(tmp_path, "m.py", _SYMBOLS_SRC)
    result = PythonAstIngestor().ingest_path(tmp_path, SCOPE)
    links = InMemoryCrossLinkIndex()

    created = link_by_footprint(
        [(MemoryRef(SCOPE, MemoryId("ep")), [("m.py", [4])])],
        result.nodes,
        links,
        repo_root=tmp_path,
    )
    assert created == 1
    ids = [n.node_id for n in links.nodes_for(MemoryRef(SCOPE, MemoryId("ep")))]
    assert ids == ["module:m"]


def test_bare_file_footprint_still_links_to_module(tmp_path: Path) -> None:
    # The honest current limit: file-only footprints (today's git per-file diff) → module-level.
    _write(tmp_path, "m.py", _SYMBOLS_SRC)
    result = PythonAstIngestor().ingest_path(tmp_path, SCOPE)
    links = InMemoryCrossLinkIndex()

    created = link_by_footprint(
        [(MemoryRef(SCOPE, MemoryId("ep")), ["m.py"])], result.nodes, links, repo_root=tmp_path
    )
    assert created == 1
    ids = [n.node_id for n in links.nodes_for(MemoryRef(SCOPE, MemoryId("ep")))]
    assert ids == ["module:m"]
