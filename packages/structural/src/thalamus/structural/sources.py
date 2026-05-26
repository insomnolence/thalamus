"""Shared Python-source discovery for the structural ingestors.

The AST structure ingestor and the jedi call resolver must see the *same* corpus
files — a call resolved into a file the structure pass never indexed cannot connect
to a node. Centralising the walk here keeps the two passes consistent.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

# Directories never part of a project's own re-derivable corpus. Hidden dirs
# (``.venv``/``.git``/``.mypy_cache``/…) are pruned by the leading dot, so this
# only needs the non-hidden noise.
IGNORE_DIRS = frozenset({"venv", "__pycache__", "build", "dist", "node_modules", "site-packages"})


def python_files(root: Path, ignore_dirs: Iterable[str] = IGNORE_DIRS) -> list[Path]:
    """Python files under ``root``, never descending into ignored or hidden dirs.

    ``root`` may be a single file (returned as-is). The corpus is the project, not
    its dependencies — the same set both ingestors operate over.
    """
    if root.is_file():
        return [root]
    ignore = frozenset(ignore_dirs)
    files: list[Path] = []
    for dirpath, dirnames, filenames in root.walk():
        dirnames[:] = [name for name in dirnames if name not in ignore and not name.startswith(".")]
        files.extend(dirpath / name for name in filenames if name.endswith(".py"))
    return sorted(files)
