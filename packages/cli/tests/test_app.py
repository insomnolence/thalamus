from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from thalamus.cli.app import main
from thalamus.instrumentation import read_trajectory_log


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_sync_subcommand_dispatches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THALAMUS_NEO4J_URI", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Tester")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "add a")

    code = main(
        [
            "sync", "--repo", str(repo), "--checkpoint", str(tmp_path / "ckpt"),
            "--encoder", "deterministic",
        ]
    )
    assert code == 0


def test_missing_subcommand_is_an_error() -> None:
    with pytest.raises(SystemExit):  # subcommand is required
        main([])


def test_capture_tests_appends_terminal_raw_event(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    report = tmp_path / "report.xml"
    report.write_text(
        '<testsuite name="s" tests="1" failures="0" errors="0" skipped="0">'
        '<testcase classname="t" name="ok"/></testsuite>',
        encoding="utf-8",
    )
    assert (
        main(
            [
                "capture-tests",
                "--repo", str(repo),
                "--junit", str(report),
                "--session-id", "s1",
                "--terminal",
            ]
        )
        == 0
    )
    (event,) = list(read_trajectory_log(repo / ".thalamus" / "logs" / "trajectory.jsonl"))
    assert event.payload["terminal"] is True
    assert str(event.session_id) == "s1"
