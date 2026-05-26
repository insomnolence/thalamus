"""Tests for resolved call edges (thalamus.structural.jedi_calls).

Skipped when the jedi extra isn't installed, mirroring the Neo4j skip pattern.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.structural import CompositeIngestor, JediCallIngestor, PythonAstIngestor

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jedi") is None, reason="requires the jedi extra"
)

SCOPE = Scope(TenantId("t"), RepoId("r"))


def _calls(root: Path) -> set[tuple[str, str]]:
    result = JediCallIngestor().ingest_path(root, SCOPE)
    return {(e.source_id, e.target_id) for e in result.edges if e.type == "calls"}


def test_resolves_intra_module_call(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "def helper():\n    return 1\n\ndef main():\n    return helper()\n", encoding="utf-8"
    )
    assert ("function:a.main", "function:a.helper") in _calls(tmp_path)


def test_resolves_cross_module_call(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text(
        "from a import helper\n\ndef use():\n    return helper()\n", encoding="utf-8"
    )
    assert ("function:b.use", "function:a.helper") in _calls(tmp_path)


def test_resolves_method_call_via_self(tmp_path: Path) -> None:
    (tmp_path / "c.py").write_text(
        "class C:\n"
        "    def m(self):\n"
        "        return self.n()\n"
        "\n"
        "    def n(self):\n"
        "        return 2\n",
        encoding="utf-8",
    )
    assert ("method:c.C.m", "method:c.C.n") in _calls(tmp_path)


def test_external_calls_produce_no_edge(tmp_path: Path) -> None:
    (tmp_path / "d.py").write_text(
        "import os\n\ndef f():\n    return os.getcwd()\n", encoding="utf-8"
    )
    assert _calls(tmp_path) == set()  # os.getcwd resolves outside the corpus


def test_recursive_call_is_not_a_self_edge(tmp_path: Path) -> None:
    (tmp_path / "e.py").write_text("def fac(n):\n    return fac(n)\n", encoding="utf-8")
    assert ("function:e.fac", "function:e.fac") not in _calls(tmp_path)


def test_ast_structure_and_jedi_calls_compose(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "def helper():\n    return 1\n\ndef main():\n    return helper()\n", encoding="utf-8"
    )
    result = CompositeIngestor([PythonAstIngestor(), JediCallIngestor()]).ingest_path(
        tmp_path, SCOPE
    )
    triples = {(e.source_id, e.target_id, e.type) for e in result.edges}
    assert ("module:a", "function:a.helper", "contains") in triples
    assert ("function:a.main", "function:a.helper", "calls") in triples
    assert "function:a.helper" in {n.node_id for n in result.nodes}
