"""Tests for footprint staleness detection (thalamus.structural.linking.footprint_staleness)."""

from __future__ import annotations

from pathlib import Path

from thalamus.core.types import MemoryId, MemoryRef, RepoId, Scope, TenantId
from thalamus.structural.linking import footprint_staleness

SCOPE = Scope(TenantId("t"), RepoId("r"))


def _ref(memory_id: str) -> MemoryRef:
    return MemoryRef(SCOPE, MemoryId(memory_id))


def test_flags_only_missing_footprint_files(tmp_path: Path) -> None:
    (tmp_path / "present.py").write_text("x = 1\n", encoding="utf-8")
    stale = footprint_staleness([(_ref("m"), ["present.py", "gone.py"])], repo_root=tmp_path)
    assert stale == {_ref("m"): ["gone.py"]}


def test_no_entry_when_all_present(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    assert footprint_staleness([(_ref("m"), ["a.py", "b.py"])], repo_root=tmp_path) == {}


def test_empty_footprint_is_not_stale(tmp_path: Path) -> None:
    assert footprint_staleness([(_ref("m"), [])], repo_root=tmp_path) == {}


def test_nested_paths(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("", encoding="utf-8")
    stale = footprint_staleness([(_ref("m"), ["pkg/mod.py", "pkg/gone.py"])], repo_root=tmp_path)
    assert stale == {_ref("m"): ["pkg/gone.py"]}
