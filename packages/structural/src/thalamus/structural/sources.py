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


def markdown_files(root: Path, ignore_dirs: Iterable[str] = IGNORE_DIRS) -> list[Path]:
    """Markdown files (``.md`` / ``.markdown``) under ``root`` — the document corpus."""
    suffixes = (".md", ".markdown")
    if root.is_file():
        return [root] if root.suffix.lower() in suffixes else []
    return _walk(root, lambda name: name.lower().endswith(suffixes), ignore_dirs)
