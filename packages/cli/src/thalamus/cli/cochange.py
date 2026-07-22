"""Build a :class:`FileCoChangeIndex` from a repo's git history — the plan tool's coupling layer.

File-level co-change (the drift-immune variant validated by ``impact-eval``: file paths are stable
across revisions, so no anchor mapping and no drift). One builder, two callers: the live serve
(``build_planner``'s optional ``cochange``) and the offline ``impact-eval`` lift measurement — so
the index the tool ships with is the same one we measured.

Live use builds from the most-recent commits (max signal, no train/test split — that split is an
eval-only guard against measuring the index against itself).
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from thalamus.core.types import Scope, StructuralRef
from thalamus.structural import FileCoChangeIndex, StructuralGraph

# Symbol kinds that anchor to source lines (TS adds interface/enum); module/doc nodes are skipped.
_SYMBOL_KINDS = ("function", "method", "class", "interface", "enum")


def code_globs(code_language: str) -> tuple[str, ...]:
    """Git pathspecs for the language's source files (TS includes .tsx)."""
    return ("*.ts", "*.tsx") if code_language == "typescript" else ("*.py",)


def git_output(repo: Path, *args: str) -> str | None:
    """Run ``git -C repo …``; ``None`` on failure (not a repo, bad ref) — never fatal."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, errors="replace", check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return result.stdout


def rel_path(path: str, repo: Path) -> str:
    """A repo-relative posix path, so graph anchors match git's repo-relative diff paths."""
    p = Path(path)
    try:
        return p.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def recent_commit_shas(repo: Path, n: int) -> list[str]:
    """The newest ``n`` commit shas (for the live, no-split co-change build)."""
    out = git_output(repo, "log", "--no-merges", "--format=%H", f"-n{n}")
    return [line.strip() for line in out.splitlines() if line.strip()] if out else []


def changed_files(repo: Path, sha: str, globs: Sequence[str] = ()) -> list[str]:
    """The files a commit changed — paths only (drift-immune). No ``globs`` = all paths."""
    pathspec = ("--", *globs) if globs else ()
    out = git_output(repo, "show", "--name-only", "--format=", sha, *pathspec)
    return [line.strip() for line in out.splitlines() if line.strip()] if out else []


def bulk_changed_files_by_sha(
    repo: Path, shas: Sequence[str], batch_size: int = 500
) -> dict[str, list[str]]:
    """Fetch changed files for multiple commit SHAs using batched git log subprocesses.

    Rather than running ``git show --name-only`` once per SHA (which spawns N process executions and
    adds tens of seconds of startup latency), this runs a single bulk ``git log --no-walk`` command
    per batch to stream all commit file sets in O(1) process executions.
    """
    if not shas:
        return {}
    sha_list = list(shas)
    res: dict[str, list[str]] = {sha: [] for sha in sha_list}
    for i in range(0, len(sha_list), batch_size):
        chunk = sha_list[i : i + batch_size]
        out = git_output(repo, "log", "--no-walk", "--format=COMMIT:%H", "--name-only", *chunk)
        if not out:
            continue
        current_sha: str | None = None
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("COMMIT:"):
                current_sha = line[7:]
            elif current_sha is not None and current_sha in res:
                res[current_sha].append(line)
    return res


def symbol_file_maps(
    graph: StructuralGraph, scope: Scope, repo: Path
) -> tuple[dict[StructuralRef, str], dict[str, list[StructuralRef]]]:
    """``ref -> file`` and ``file -> [ref]`` over code symbols, from the graph's own anchors."""
    ref_file: dict[StructuralRef, str] = {}
    file_refs: dict[str, list[StructuralRef]] = {}
    for kind in _SYMBOL_KINDS:
        for node in graph.nodes_of_kind(scope, kind):
            if node.anchor is None:
                continue
            path = rel_path(node.anchor.path, repo)
            ref_file[node.ref] = path
            file_refs.setdefault(path, []).append(node.ref)
    return ref_file, file_refs


def build_file_cochange(
    repo: Path,
    graph: StructuralGraph,
    scope: Scope,
    shas: Sequence[str],
) -> FileCoChangeIndex:
    """Accumulate file co-change over ``shas`` into a :class:`FileCoChangeIndex`.

    **Language-agnostic by construction:** each commit's changed files are filtered to those that
    carry a code symbol in the current graph (the ``file_refs`` keys), so co-change tracks exactly
    the code files Brain 2 knows — no language glob. Symbol↔file membership comes from the
    graph anchors; a commit touching <2 known code files yields no pair and is skipped."""
    ref_file, file_refs = symbol_file_maps(graph, scope, repo)
    index = FileCoChangeIndex(ref_file=ref_file, file_refs=file_refs)
    if not shas:
        return index

    file_sets = bulk_changed_files_by_sha(repo, shas)
    for sha in shas:
        files = [path for path in file_sets.get(sha, []) if path in file_refs]
        if len(files) >= 2:
            index.add_commit(files)
    return index
