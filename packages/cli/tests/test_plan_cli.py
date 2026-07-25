"""``thalamus plan`` — the CLI surface for the plan tool.

The flagship brief must be reachable without an MCP client, and the CLI must render the *same*
brief the served tool does (both go through ``serve.build_plan_reader``).
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest
from thalamus.cli.app import main
from thalamus.cli.plan import add_plan_arguments, plan_config


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo_with_a_call_edge(tmp_path: Path) -> Path:
    """A tiny git repo whose code has a real caller → callee edge to put in a blast radius."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Tester")
    (repo / "core.py").write_text(
        "def encode_payload(value):\n"
        "    return str(value)\n",
        encoding="utf-8",
    )
    (repo / "caller.py").write_text(
        "from core import encode_payload\n"
        "\n"
        "def handle(request):\n"
        "    return encode_payload(request)\n",
        encoding="utf-8",
    )
    _git(repo, "add", "core.py", "caller.py")
    _git(repo, "commit", "-q", "-m", "add encoder and its caller")
    return repo


def test_plan_config_defaults_repo_id_to_the_repo_dir_name(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    add_plan_arguments(parser)
    repo = tmp_path / "myproject"
    repo.mkdir()
    config = plan_config(parser.parse_args(["encode_payload", "--repo", str(repo)]))
    assert config.repo_id == "myproject"
    assert config.target == "encode_payload"
    assert config.hops == 2
    # Mirrors `serve` so the CLI brief is the full one, not a call-graph-only variant.
    assert config.plan_cochange_commits == 500


def test_plan_subcommand_renders_a_brief_for_a_resolvable_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("THALAMUS_NEO4J_URI", raising=False)
    monkeypatch.delenv("THALAMUS_TEST_NEO4J_URI", raising=False)
    repo = _repo_with_a_call_edge(tmp_path)

    code = main(["plan", "encode_payload", "--repo", str(repo), "--encoder", "deterministic"])

    assert code == 0
    out = capsys.readouterr().out
    assert "# Plan brief: encode_payload" in out
    # Coverage honesty is the section that must never silently vanish: a brief that omits it
    # reads as "no constraints" when the truth may be "no data here" (the §14 discipline).
    assert "## Coverage" in out


def test_plan_subcommand_is_honest_when_the_target_does_not_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("THALAMUS_NEO4J_URI", raising=False)
    monkeypatch.delenv("THALAMUS_TEST_NEO4J_URI", raising=False)
    repo = _repo_with_a_call_edge(tmp_path)

    code = main(
        ["plan", "no_such_symbol_anywhere", "--repo", str(repo), "--encoder", "deterministic"]
    )

    assert code == 0  # an unresolvable target is a reportable answer, not a CLI failure
    out = capsys.readouterr().out
    assert "# Plan brief: no_such_symbol_anywhere" in out
