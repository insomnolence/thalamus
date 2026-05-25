from __future__ import annotations

from pathlib import Path

from thalamus.structural import PythonAstIngestor

SOURCE = (
    "import os\n"
    "from a.b import thing\n"
    "\n"
    "class Animal:\n"
    "    def speak(self):\n"
    "        return 1\n"
    "\n"
    "class Dog(Animal):\n"
    "    def bark(self):\n"
    "        return 2\n"
    "\n"
    "def helper():\n"
    "    return 3\n"
)


def test_ingest_python_file(tmp_path: Path) -> None:
    src = tmp_path / "mymod.py"
    src.write_text(SOURCE, encoding="utf-8")
    result = PythonAstIngestor().ingest_path(src)

    ids = {node.node_id for node in result.nodes}
    assert {
        "module:mymod",
        "class:mymod.Animal",
        "method:mymod.Animal.speak",
        "class:mymod.Dog",
        "method:mymod.Dog.bark",
        "function:mymod.helper",
    } <= ids

    edges = {(e.source_id, e.type, e.target_id) for e in result.edges}
    assert ("module:mymod", "contains", "class:mymod.Animal") in edges
    assert ("class:mymod.Animal", "contains", "method:mymod.Animal.speak") in edges
    assert ("class:mymod.Dog", "inherits", "class:mymod.Animal") in edges
    assert ("module:mymod", "imports", "module:os") in edges
    assert ("module:mymod", "imports", "module:a.b") in edges

    animal = next(n for n in result.nodes if n.node_id == "class:mymod.Animal")
    assert animal.anchor is not None
    assert animal.anchor.line_start == 4
    assert animal.metadata["bases"] == []
    dog = next(n for n in result.nodes if n.node_id == "class:mymod.Dog")
    assert dog.metadata["bases"] == ["Animal"]


def test_ingest_directory_with_root_package(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    result = PythonAstIngestor(root_package="pkg").ingest_path(pkg)
    ids = {node.node_id for node in result.nodes}
    assert "function:pkg.core.f" in ids
    assert "module:pkg" in ids


def test_syntax_error_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text("def g():\n    return 1\n", encoding="utf-8")
    result = PythonAstIngestor().ingest_path(tmp_path)
    ids = {node.node_id for node in result.nodes}
    assert "function:ok.g" in ids
