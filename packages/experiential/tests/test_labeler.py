"""Tests for the negative-signal labeler (thalamus.experiential.labeler).

Two layers: the pure ``region_fate`` combiner (edge cases, no I/O) and the ``GitSurvivalLabeler``
git archaeology against a real temp repo (overwrite → churn, append/survive → exercised survival).
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.types import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.experiential.fate import _DEFAULT_CHURN_THRESHOLD
from thalamus.experiential.labeler import FateLabels, GitSurvivalLabeler, region_fate

SCOPE = Scope(tenant_id=TenantId("t"), repo_id=RepoId("r"))
NOW = datetime(2026, 6, 15, tzinfo=UTC)


# ── the pure combiner ─────────────────────────────────────────────────────────────────────────


def test_no_introduced_lines_is_no_evidence() -> None:
    assert region_fate(introduced=0, surviving=0, exercising_commits=3) == (0.0, 0)


def test_full_survival_credits_exercising_commits() -> None:
    churn, survived = region_fate(introduced=10, surviving=10, exercising_commits=4)
    assert churn == 0.0
    assert survived == 4  # held intact → the activity it withstood counts


def test_full_overwrite_is_max_churn_and_no_survival() -> None:
    churn, survived = region_fate(introduced=10, surviving=0, exercising_commits=4)
    assert churn == 1.0
    assert survived == 0  # churned away → survived nothing; churn carries the negative


def test_partial_below_threshold_still_counts_as_survived() -> None:
    churn, survived = region_fate(introduced=10, surviving=8, exercising_commits=3)
    assert abs(churn - 0.2) < 1e-9
    assert survived == 3  # churn < 0.7 → it held


def test_partial_above_threshold_loses_survival() -> None:
    churn, survived = region_fate(introduced=10, surviving=2, exercising_commits=3)
    assert abs(churn - 0.8) < 1e-9
    assert survived == 0


def test_churn_exactly_at_threshold_does_not_count_as_survived() -> None:
    # churn == threshold is NOT < threshold → no survival credit (the boundary is exclusive).
    churn, survived = region_fate(introduced=10, surviving=3, exercising_commits=3)
    assert abs(churn - _DEFAULT_CHURN_THRESHOLD) < 1e-9
    assert survived == 0


def test_surviving_is_clamped_to_introduced() -> None:
    # blame can't attribute more lines than were introduced; a stray over-count clamps to 0 churn.
    churn, survived = region_fate(introduced=10, surviving=15, exercising_commits=2)
    assert churn == 0.0
    assert survived == 2


# ── the git labeler ───────────────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Tester")
    return repo


def _commit(repo: Path, name: str, body: str, message: str) -> str:
    (repo / name).write_text(body, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _episode(sha: str, files: list[str]) -> MemoryRecord:
    return MemoryRecord(
        MemoryId(f"episode:{sha}"), Hemisphere.EXPERIENTIAL, "episode", "work", SCOPE, NOW,
        metadata={"footprint": files},
    )


def test_survived_code_has_low_churn_and_counts_exercising_commits(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    sha = _commit(repo, "m.py", "a\nb\nc\nd\ne\n", "introduce m")  # 5 lines introduced
    # two later commits APPEND to m.py — they exercise the footprint but don't overwrite the 5 lines
    _commit(repo, "m.py", "a\nb\nc\nd\ne\nf\n", "extend m")
    _commit(repo, "m.py", "a\nb\nc\nd\ne\nf\ng\n", "extend m again")

    labels = GitSurvivalLabeler(repo).label([_episode(sha, ["m.py"])])
    mid = MemoryId(f"episode:{sha}")
    assert labels.churn_ratio[mid] == 0.0  # all 5 original lines survive at HEAD
    assert labels.survived_activity[mid] == 2  # withstood the two later footprint-touching commits


def test_overwritten_code_is_high_churn_and_no_survival(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    sha = _commit(repo, "m.py", "a\nb\nc\nd\ne\n", "introduce m")
    _commit(repo, "m.py", "X\nY\nZ\nP\nQ\n", "rewrite m wholesale")  # overwrite every line

    labels = GitSurvivalLabeler(repo).label([_episode(sha, ["m.py"])])
    mid = MemoryId(f"episode:{sha}")
    assert labels.churn_ratio[mid] == 1.0  # none of the original lines survive
    assert labels.survived_activity[mid] == 0


def test_curated_and_footprintless_memories_are_skipped(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    sha = _commit(repo, "m.py", "a\nb\n", "introduce m")
    curated = MemoryRecord(
        MemoryId("retained:abc123"), Hemisphere.EXPERIENTIAL, "decision", "x", SCOPE, NOW,
        metadata={"footprint": ["m.py"]},
    )
    no_footprint = _episode(sha, [])
    # neither is a trackable region (no episode sha / no footprint)
    labels = GitSurvivalLabeler(repo).label([curated, no_footprint])
    assert labels == FateLabels(churn_ratio={}, survived_activity={})


def test_unknown_sha_is_skipped_not_fatal(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "m.py", "a\nb\n", "introduce m")
    ghost = _episode("0" * 40, ["m.py"])  # a sha not in this repo's history
    labels = GitSurvivalLabeler(repo).label([ghost])
    assert labels.churn_ratio == {}  # missing data → skipped, never raises


def test_non_utf8_file_in_footprint_does_not_crash(tmp_path: Path) -> None:
    # Real-data regression: a binary/non-utf8 file in a commit's footprint must not crash blame's
    # decode (git output is read with errors="replace").
    repo = _repo(tmp_path)
    (repo / "data.bin").write_bytes(b"\x00\xff\xfe\x01blob\x80\n")
    (repo / "m.py").write_text("a\nb\nc\n", encoding="utf-8")
    _git(repo, "add", "data.bin", "m.py")
    _git(repo, "commit", "-q", "-m", "add code + a binary blob")
    sha = _git(repo, "rev-parse", "HEAD").strip()

    labels = GitSurvivalLabeler(repo).label([_episode(sha, ["m.py", "data.bin"])])
    mid = MemoryId(f"episode:{sha}")
    assert mid in labels.churn_ratio  # computed over the text lines, binary tolerated
