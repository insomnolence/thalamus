from __future__ import annotations

import subprocess
from pathlib import Path

from thalamus.core.types import EventId, RepoId, Scope, TenantId
from thalamus.instrumentation import GitObserver, TrajectoryEventKind, reverted_shas
from thalamus.instrumentation.git_observer import _parse_changed_lines

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


def test_parse_changed_lines_reads_new_side_hunks() -> None:
    diff = (
        "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n"
        "@@ -0,0 +1,3 @@\n+a\n+b\n+c\n"
        "@@ -10 +12,2 @@\n+x\n+y\n"
    )
    assert _parse_changed_lines(diff) == {"foo.py": [1, 2, 3, 12, 13]}


def test_parse_changed_lines_skips_deletions_and_zero_count_hunks() -> None:
    diff = (
        "diff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n@@ -1,5 +0,0 @@\n"
        "diff --git a/keep.py b/keep.py\n--- a/keep.py\n+++ b/keep.py\n@@ -2 +2,0 @@\n"
    )
    assert _parse_changed_lines(diff) == {}  # deleted file + a zero-count hunk → no new-side lines


def test_poll_captures_changed_line_ranges(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "m.py").write_text("l1\nl2\nl3\n", encoding="utf-8")
    _git(repo, "add", "m.py")
    _git(repo, "commit", "-q", "-m", "add m")
    observer = GitObserver(repo, SCOPE, event_id_factory=_ids)
    event = observer.poll()[0]
    assert event.payload["file_lines"] == {"m.py": [1, 2, 3]}


def test_poll_since_only_new_commits(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "a.py", "add a")
    observer = GitObserver(repo, SCOPE, event_id_factory=_ids)
    first_head = observer.head()
    _commit(repo, "b.py", "add b")
    events = observer.poll(since=first_head)
    assert [e.payload["subject"] for e in events] == ["add b"]
    assert events[0].payload["files"] == ["b.py"]


def _head(repo: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def test_reverted_shas_detects_an_explicit_revert(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "a.py", "add a")
    _commit(repo, "b.py", "add b")
    reverted = _head(repo)  # the sha of "add b", which we now revert
    _git(repo, "revert", "--no-edit", "HEAD")
    assert reverted in reverted_shas(repo)


def test_reverted_shas_empty_without_reverts(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "a.py", "add a")
    assert reverted_shas(repo) == frozenset()


def test_reverted_shas_on_non_repo_is_empty(tmp_path: Path) -> None:
    assert reverted_shas(tmp_path / "nope") == frozenset()
