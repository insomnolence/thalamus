from __future__ import annotations

from pathlib import Path

from thalamus.core.types import MemoryId
from thalamus.structural import InMemoryCrossLinkIndex, PythonAstIngestor, link_by_footprint


def _write(repo: Path, rel: str, src: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(src, encoding="utf-8")


def test_links_episode_to_touched_module(tmp_path: Path) -> None:
    _write(tmp_path, "foo.py", "def do_thing():\n    return 1\n")
    _write(tmp_path, "bar.py", "def other():\n    return 2\n")
    result = PythonAstIngestor().ingest_path(tmp_path)
    links = InMemoryCrossLinkIndex()

    created = link_by_footprint(
        [(MemoryId("ep1"), ["foo.py"])], result.nodes, links, repo_root=tmp_path
    )
    assert created == 1
    assert links.nodes_for(MemoryId("ep1")) == ["module:foo"]  # touched foo, not bar
    assert MemoryId("ep1") in links.memories_for("module:foo")


def test_absolute_anchor_matches_relative_footprint(tmp_path: Path) -> None:
    # anchors are absolute (ingested from an absolute root); footprints are repo-relative
    _write(tmp_path, "sub/mod.py", "def f():\n    return 0\n")
    result = PythonAstIngestor().ingest_path(tmp_path)
    module = next(node for node in result.nodes if node.kind == "module")
    assert module.anchor is not None and Path(module.anchor.path).is_absolute()

    links = InMemoryCrossLinkIndex()
    created = link_by_footprint(
        [(MemoryId("e"), ["sub/mod.py"])], result.nodes, links, repo_root=tmp_path
    )
    assert created == 1


def test_non_python_or_unmatched_files_do_not_link(tmp_path: Path) -> None:
    _write(tmp_path, "foo.py", "x = 1\n")
    result = PythonAstIngestor().ingest_path(tmp_path)
    links = InMemoryCrossLinkIndex()

    created = link_by_footprint(
        [(MemoryId("ep"), ["README.md", "missing.py"])], result.nodes, links, repo_root=tmp_path
    )
    assert created == 0  # links are never forced
    assert links.nodes_for(MemoryId("ep")) == []


def test_idempotent_relink(tmp_path: Path) -> None:
    _write(tmp_path, "foo.py", "y = 1\n")
    result = PythonAstIngestor().ingest_path(tmp_path)
    links = InMemoryCrossLinkIndex()
    items = [(MemoryId("ep"), ["foo.py"])]

    link_by_footprint(items, result.nodes, links, repo_root=tmp_path)
    link_by_footprint(items, result.nodes, links, repo_root=tmp_path)  # re-run
    assert links.nodes_for(MemoryId("ep")) == ["module:foo"]  # no duplicate edge
