"""Shared corpus-file discovery for the structural ingestors.

The ingestors (Python AST, jedi calls, Markdown docs) must see a consistent set of
corpus files. Centralising the walk here keeps them aligned and never descends into
ignored or hidden directories — the corpus is the project, not its dependencies.
"""

from __future__ import annotations

import fnmatch
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


def _rel(path: Path, root: Path) -> str:
    """A node's stable relative-id segment: ``path`` under ``root`` (bare name for a file root).

    Shared by the document/text ingestors so their node ids are computed identically — a
    POSIX-style relative path, falling back to the bare filename when ``path`` is outside
    ``root`` or ``root`` is itself a single file."""
    if root.is_file():
        return path.name
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


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


def text_files(
    root: Path,
    ignore_dirs: Iterable[str] = IGNORE_DIRS,
    *,
    suffixes: tuple[str, ...] = (".txt",),
) -> list[Path]:
    """Plain-text files (``.txt`` by default) under ``root`` — the generic text corpus.

    The headingless counterpart of :func:`markdown_files`: it drives the generic
    :class:`~thalamus.structural.text_ingestor.TextIngestor`. ``suffixes`` widens the set
    (e.g. ``(".txt", ".log")``) without a new walker."""
    lowered = tuple(s.lower() for s in suffixes)
    if root.is_file():
        return [root] if root.suffix.lower() in lowered else []
    return _walk(root, lambda name: name.lower().endswith(lowered), ignore_dirs)


def _is_typescript_source(name: str) -> bool:
    """A ``.ts``/``.tsx`` source file — excluding declaration files and tests."""
    if not name.endswith((".ts", ".tsx")):
        return False
    if name.endswith(".d.ts"):  # ambient type declarations — not project structure
        return False
    return not name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))


def glob_files(
    *patterns: str, ignore_dirs: Iterable[str] = IGNORE_DIRS
) -> Callable[[Path], list[Path]]:
    """A corpus-file enumerator matching filename glob ``patterns`` under a root.

    The language-agnostic counterpart of the built-in Python/TS/Markdown walkers: it lets a
    declarative ``[[corpus]]`` config name its own source files for any language —
    ``glob_files("*.rs")`` (Rust), ``glob_files("*.cpp", "*.hpp", "*.h")`` (C++), etc. A file
    matches when its *name* matches any pattern; the same ignored/hidden dirs are pruned. Used for
    change detection — for a SCIP corpus the ingestor reads the prebuilt index, but the enumerator
    must hash the on-disk source so a code edit triggers a re-derive."""
    pats = tuple(patterns)

    def accept(name: str) -> bool:
        return any(fnmatch.fnmatch(name, pat) for pat in pats)

    def enumerate_files(root: Path, ignore: Iterable[str] = ignore_dirs) -> list[Path]:
        if root.is_file():
            return [root] if accept(root.name) else []
        return _walk(root, accept, ignore)

    return enumerate_files


def typescript_files(root: Path, ignore_dirs: Iterable[str] = IGNORE_DIRS) -> list[Path]:
    """TypeScript source for the structural **code corpus** — project ``.ts``/``.tsx``
    minus declaration files (``.d.ts``) and tests.

    The SCIP code corpus counterpart of :func:`code_files`: it drives change detection
    for ``incremental_ingest`` (the ingestor reads the prebuilt index, but the enumerator
    hashes the source on disk), so the paths it yields must match the ingestor's
    ``anchor.path`` (``root / relative_path``). Tests/declarations are excluded for the
    same reason as Python: they are semantic noise for structural retrieval."""
    if root.is_file():
        return [root] if _is_typescript_source(root.name) else []
    return _walk(root, _is_typescript_source, frozenset(ignore_dirs) | _TEST_DIRS)
