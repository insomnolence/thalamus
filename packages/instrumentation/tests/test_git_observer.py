from __future__ import annotations

import subprocess
from pathlib import Path

from thalamus.core.types import EventId, RepoId, Scope, TenantId
from thalamus.instrumentation import GitObserver, TrajectoryEventKind

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Tester")
    return repo


def _commit(repo: Path, name: str, message: str) -> None:
    (repo / name).write_text("content\n", encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)


def _ids() -> EventId:
    return EventId("e")


def test_empty_repo_yields_nothing(tmp_path: Path) -> None:
    observer = GitObserver(_repo(tmp_path), SCOPE, event_id_factory=_ids)
    assert observer.poll() == []


def test_poll_emits_commit_events(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "a.py", "add a")
    observer = GitObserver(repo, SCOPE, event_id_factory=_ids)
    events = observer.poll()
    assert len(events) == 1
    assert events[0].kind is TrajectoryEventKind.COMMIT
    assert events[0].payload["subject"] == "add a"
    assert events[0].payload["files"] == ["a.py"]


def test_poll_since_only_new_commits(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "a.py", "add a")
    observer = GitObserver(repo, SCOPE, event_id_factory=_ids)
    first_head = observer.head()
    _commit(repo, "b.py", "add b")
    events = observer.poll(since=first_head)
    assert [e.payload["subject"] for e in events] == ["add b"]
    assert events[0].payload["files"] == ["b.py"]
