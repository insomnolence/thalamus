"""GitObserver — out-of-band, actuator-agnostic trajectory capture from git.

Reads commits via the ``git`` CLI and emits ``COMMIT`` trajectory events (sha,
subject, changed files). Deterministic and requires no actuator cooperation —
the spine of the trajectory log (foundation.md / OLR §13.11b). The file-watcher
(edits/dead-ends) and pytest-hook (test fail→pass) observers build on this same
pattern: produce :class:`TrajectoryEvent`s, hand them to a sink.
"""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.types import EventId, Scope
from thalamus.instrumentation.trajectory import TrajectoryEvent, TrajectoryEventKind

_UNIT = "\x1f"  # ASCII unit separator — safe field delimiter for git --format


def _uuid_event_id() -> EventId:
    return EventId(uuid.uuid4().hex)


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
            events.append(
                TrajectoryEvent(
                    event_id=self._event_id_factory(),
                    timestamp=datetime.fromtimestamp(int(committed_at), tz=UTC),
                    scope=self._scope,
                    kind=TrajectoryEventKind.COMMIT,
                    payload={"sha": sha, "subject": subject, "files": files},
                )
            )
        return events
