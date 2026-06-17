"""GitObserver — out-of-band, actuator-agnostic trajectory capture from git.

Reads commits via the ``git`` CLI and emits ``COMMIT`` trajectory events (sha,
subject, changed files). Deterministic and requires no actuator cooperation —
the spine of the trajectory log (foundation.md / OLR §13.11b). The file-watcher
(edits/dead-ends) and pytest-hook (test fail→pass) observers build on this same
pattern: produce :class:`TrajectoryEvent`s, hand them to a sink.
"""

from __future__ import annotations

import re
import subprocess
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.types import EventId, Scope
from thalamus.instrumentation.trajectory import TrajectoryEvent, TrajectoryEventKind

_UNIT = "\x1f"  # ASCII unit separator — safe field delimiter for git --format

# A unified-diff hunk header: ``@@ -old[,n] +new[,m] @@`` — we want the new-side start + count.
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _uuid_event_id() -> EventId:
    return EventId(uuid.uuid4().hex)


def _parse_changed_lines(diff_text: str) -> dict[str, list[int]]:
    """New-side changed line numbers per file, parsed from a ``--unified=0`` diff (C-8).

    Reads ``+++ b/<path>`` file headers and ``@@ … +start[,count] @@`` hunk headers; a hunk with
    count 0 (a pure deletion) contributes no new-side lines, and ``+++ /dev/null`` (a deleted file)
    contributes none either. The line ranges let a memory's footprint link to the *symbol* it
    touched, not just the file (C-7). Pure and tolerant of odd/empty diffs."""
    lines_by_file: dict[str, list[int]] = {}
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target == "/dev/null":
                current = None
            else:
                current = target[2:] if target[:2] in ("a/", "b/") else target
            continue
        if current is None:
            continue
        match = _HUNK_RE.match(line)
        if match is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        if count > 0:
            lines_by_file.setdefault(current, []).extend(range(start, start + count))
    return {path: sorted(set(nums)) for path, nums in lines_by_file.items() if nums}


class GitObserver:
    """Emits COMMIT trajectory events for commits in a repository.

    Stateless: the caller passes ``since`` (a ref/sha) to get commits after it,
    so checkpointing is the caller's concern. Commit timestamps come from git, so
    events are faithful to when work happened, not when they were observed.
    """

    def __init__(
        self,
        repo_path: Path,
        scope: Scope,
        *,
        event_id_factory: Callable[[], EventId] = _uuid_event_id,
    ) -> None:
        self._repo = repo_path
        self._scope = scope
        self._event_id_factory = event_id_factory

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self._repo), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def head(self) -> str | None:
        """Return the current HEAD sha, or ``None`` if the repo has no commits."""
        try:
            return self._git("rev-parse", "HEAD").strip()
        except subprocess.CalledProcessError:
            return None

    def poll(self, since: str | None = None) -> list[TrajectoryEvent]:
        """Return COMMIT events for commits after ``since`` (or all commits)."""
        if self.head() is None:
            return []
        rev_range = f"{since}..HEAD" if since else "HEAD"
        log = self._git("log", rev_range, "--reverse", f"--format=%H{_UNIT}%ct{_UNIT}%s")
        events: list[TrajectoryEvent] = []
        for line in log.splitlines():
            if not line.strip():
                continue
            sha, committed_at, subject = line.split(_UNIT, 2)
            files = [
                name
                for name in self._git(
                    "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha
                ).splitlines()
                if name
            ]
            # C-8: per-file changed line ranges (additive) so a footprint can link to the symbol it
            # touched, not just the file. A second diff with zero context — files without parseable
            # hunks (binary, pure rename) simply carry no lines and fall back to module linking.
            file_lines = _parse_changed_lines(
                self._git("diff-tree", "--no-commit-id", "-r", "--root", "--unified=0", sha)
            )
            events.append(
                TrajectoryEvent(
                    event_id=self._event_id_factory(),
                    timestamp=datetime.fromtimestamp(int(committed_at), tz=UTC),
                    scope=self._scope,
                    kind=TrajectoryEventKind.COMMIT,
                    payload={
                        "sha": sha,
                        "subject": subject,
                        "files": files,
                        "file_lines": file_lines,
                    },
                )
            )
        return events


_REVERTS_RE = re.compile(r"[Tt]his reverts commit ([0-9a-f]{7,40})")


def _reverted_from_log(log_text: str) -> frozenset[str]:
    """The original shas named by ``This reverts commit <sha>`` lines in commit bodies. Pure."""
    return frozenset(match.group(1) for match in _REVERTS_RE.finditer(log_text))


def reverted_shas(repo_path: Path) -> frozenset[str]:
    """Commit shas a later ``git revert`` undid, read from history — deterministic, out-of-band.

    The intentional, costly-to-fake negative Tier-2 signal (OLR §13.8): work *deliberately* undone.
    Reads commit bodies for git's ``This reverts commit <sha>`` line. Force-pushed / reset history
    is invisible (those commits are gone) — only explicit reverts are detected, which is the signal
    we trust. Returns an empty set if ``repo_path`` is not a git repo."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "log", "--format=%b"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return frozenset()
    return _reverted_from_log(result.stdout)
