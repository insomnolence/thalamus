"""Tests for the canonical structural id scheme (thalamus.structural.ids)."""

from __future__ import annotations

from pathlib import Path

from thalamus.structural.ids import class_id, function_id, method_id, module_dotted, module_id


def test_module_dotted_from_relative_path(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    f = tmp_path / "pkg" / "mod.py"
    f.write_text("", encoding="utf-8")
    assert module_dotted(f, tmp_path) == "pkg.mod"


def test_module_dotted_strips_init(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    f = tmp_path / "pkg" / "__init__.py"
    f.write_text("", encoding="utf-8")
    assert module_dotted(f, tmp_path) == "pkg"


def test_module_dotted_single_file(tmp_path: Path) -> None:
    f = tmp_path / "backend.py"
    f.write_text("", encoding="utf-8")
    assert module_dotted(f, f) == "backend"


def test_module_dotted_root_package_prefix(tmp_path: Path) -> None:
    f = tmp_path / "mod.py"
    f.write_text("", encoding="utf-8")
    assert module_dotted(f, tmp_path, "acme") == "acme.mod"


def test_id_helpers() -> None:
    assert module_id("pkg.mod") == "module:pkg.mod"
    assert class_id("pkg.mod", "C") == "class:pkg.mod.C"
    assert function_id("pkg.mod", "f") == "function:pkg.mod.f"
    assert method_id("pkg.mod", "C", "m") == "method:pkg.mod.C.m"
