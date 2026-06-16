from __future__ import annotations

from pathlib import Path

from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.structural import PythonAstIngestor, SymbolResolver

SCOPE = Scope(TenantId("t"), RepoId("r"))

_SRC = """\
import os


def top_level():
    return 1


class Widget:
    def make(self):
        x = 1
        return x

    def teardown(self):
        return 2
"""


def _resolver(tmp_path: Path) -> SymbolResolver:
    (tmp_path / "w.py").write_text(_SRC, encoding="utf-8")
    nodes = PythonAstIngestor().ingest_path(tmp_path, SCOPE).nodes
    return SymbolResolver(nodes, repo_root=tmp_path)


def test_resolves_line_to_smallest_enclosing_symbol(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)
    # line 10 is inside Widget.make — the method, not the class, wins (smallest enclosing).
    node = resolver.resolve("w.py", 10)
    assert node is not None and node.node_id == "method:w.Widget.make"


def test_resolves_class_body_line_outside_methods_to_class(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)
    # line 8 is the class header, enclosed by Widget but not by any method.
    node = resolver.resolve("w.py", 8)
    assert node is not None and node.node_id == "class:w.Widget"


def test_resolves_top_level_function_line(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)
    node = resolver.resolve("w.py", 5)
    assert node is not None and node.node_id == "function:w.top_level"


def test_no_line_falls_back_to_module(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)
    node = resolver.resolve("w.py", None)
    assert node is not None and node.node_id == "module:w"


def test_module_line_outside_any_symbol_falls_back_to_module(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)
    # line 1 (the import) is enclosed by no symbol → module fallback.
    node = resolver.resolve("w.py", 1)
    assert node is not None and node.node_id == "module:w"


def test_unknown_file_resolves_to_none(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)
    assert resolver.resolve("nope.py", 3) is None
    assert resolver.resolve("nope.py", None) is None


def test_relative_and_absolute_paths_resolve_identically(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)
    absolute = str((tmp_path / "w.py").resolve())
    assert resolver.resolve(absolute, 10) == resolver.resolve("w.py", 10)
