"""Shared corpus-file discovery for the structural ingestors.

The ingestors (Python AST, jedi calls, Markdown docs) must see a consistent set of
corpus files. Centralising the walk here keeps them aligned and never descends into
ignored or hidden directories — the corpus is the project, not its dependencies.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

# Directories never part of a project's own re-derivable corpus. Hidden dirs
# (``.venv``/``.git``/``.mypy_cache``/…) are pruned by the leading dot, so this
# only needs the non-hidden noise.
IGNORE_DIRS = frozenset({"venv", "__pycache__", "build", "dist", "node_modules", "site-packages"})

# Test directories + filename patterns. Tests are part of the project on disk, but they are
# semantic noise for "how does X work" structural retrieval (they embed near the terms of the
# code they exercise) and would crowd the gateway's structural slots — so the CODE corpus
# (``code_files``) excludes them. The general ``python_files`` walker still returns everything.
_TEST_DIRS = frozenset({"tests", "test"})


def _is_test_filename(name: str) -> bool:
    return name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py")


def _walk(root: Path, accept: Callable[[str], bool], ignore_dirs: Iterable[str]) -> list[Path]:
    ignore = frozenset(ignore_dirs)
    files: list[Path] = []
    for dirpath, dirnames, filenames in root.walk():
        dirnames[:] = [name for name in dirnames if name not in ignore and not name.startswith(".")]
        files.extend(dirpath / name for name in filenames if accept(name))
    return sorted(files)


def python_files(root: Path, ignore_dirs: Iterable[str] = IGNORE_DIRS) -> list[Path]:
    """Python files under ``root`` (or ``root`` itself if it is a single file)."""
    if root.is_file():
        return [root]
    return _walk(root, lambda name: name.endswith(".py"), ignore_dirs)


def code_files(root: Path, ignore_dirs: Iterable[str] = IGNORE_DIRS) -> list[Path]:
    """Python files for the structural **code corpus** — project source *minus tests*.

    Excludes ``tests``/``test`` directories and ``test_*.py`` / ``*_test.py`` / ``conftest.py``
    so test functions stop surfacing as "Related code" noise on conceptual queries. Tests stay
    on disk and findable; this only governs what Brain 2 indexes. (Trade-off: Brain 2 loses
    test nodes, so the call graph no longer shows "called by test_…"; a separate ``tests`` corpus
    is the future refinement if that coverage signal is wanted.)"""
    if root.is_file():
        keep = root.name.endswith(".py") and not _is_test_filename(root.name)
        return [root] if keep else []
    return _walk(
        root,
        lambda name: name.endswith(".py") and not _is_test_filename(name),
        frozenset(ignore_dirs) | _TEST_DIRS,
    )


def markdown_files(root: Path, ignore_dirs: Iterable[str] = IGNORE_DIRS) -> list[Path]:
    """Markdown files (``.md`` / ``.markdown``) under ``root`` — the document corpus."""
    suffixes = (".md", ".markdown")
    if root.is_file():
        return [root] if root.suffix.lower() in suffixes else []
    return _walk(root, lambda name: name.lower().endswith(suffixes), ignore_dirs)
