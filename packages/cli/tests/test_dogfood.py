from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from thalamus.cli import SyncConfig, parse_args, run_sync
from thalamus.instrumentation import read_trajectory_log


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo_with_commit(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Tester")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "add a")
    return repo


def _config(repo: Path, checkpoint: Path) -> SyncConfig:
    return SyncConfig(
        repo=repo, checkpoint=checkpoint, tenant="local", repo_id="r", dim=64,
        encoder="deterministic",
        neo4j_uri=None, neo4j_user="neo4j", neo4j_password=None,
    )


def test_parse_args_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THALAMUS_NEO4J_URI", raising=False)
    config = parse_args(["--repo", str(tmp_path), "--tenant", "acme", "--encoder", "deterministic"])
    assert config.repo == tmp_path.resolve()
    assert config.repo_id == tmp_path.name  # defaults to the repo dir name
    assert config.checkpoint == tmp_path.resolve() / ".thalamus" / "checkpoints" / "git.cursor"
    assert config.tenant == "acme"
    assert config.neo4j_uri is None  # in-memory fallback when env is unset


def test_parse_args_reads_neo4j_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THALAMUS_NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("THALAMUS_NEO4J_PASSWORD", "pw")
    config = parse_args(["--encoder", "deterministic"])
    assert config.neo4j_uri == "bolt://localhost:7687"
    assert config.neo4j_password == "pw"


def test_run_sync_ingests_real_commit_and_checkpoints(tmp_path: Path) -> None:
    repo = _repo_with_commit(tmp_path)
    checkpoint = tmp_path / "ckpt" / "git.cursor"
    config = _config(repo, checkpoint)

    records = run_sync(config)
    assert len(records) == 1
    assert records[0].content.startswith("Worked toward: add a")
    assert checkpoint.exists()  # cursor persisted

    # re-running rebuilds the derived record from the durable raw log without duplication.
    assert len(run_sync(config)) == 1
    trajectory = repo / ".thalamus" / "logs" / "trajectory.jsonl"
    assert trajectory.exists()
    assert len(list(read_trajectory_log(trajectory))) == 1
