from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from thalamus.cli.backup import (
    BackupConfig,
    RestoreConfig,
    add_backup_arguments,
    add_restore_arguments,
    backup_config,
    restore_config,
    run_backup,
    run_restore,
)
from thalamus.core.types import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.store import InMemoryStore

NOW = datetime(2026, 5, 27, tzinfo=UTC)
OTHER = Scope(TenantId("local"), RepoId("elsewhere"))


def _scope(tmp_path: Path) -> Scope:
    # backup_config derives repo_id from the repo dir name; mirror it so the seeded
    # records share the scope the command will back up.
    return Scope(TenantId("local"), RepoId(tmp_path.name))


def _record(mid: str, kind: str, scope: Scope) -> MemoryRecord:
    return MemoryRecord(
        MemoryId(mid), Hemisphere.EXPERIENTIAL, kind, f"content of {mid}", scope, NOW,
        metadata={"footprint": ["a.py"], "importance": 2.0},
    )


def _seed(scope: Scope) -> InMemoryStore:
    store = InMemoryStore(dim=3)
    store.add(_record("retained:d1", "decision", scope), [1.0, 0.0, 0.0])
    store.add(_record("retained:g1", "gotcha", scope), [0.0, 1.0, 0.0])
    store.add(_record("episode:abc", "episode", scope), [0.0, 0.0, 1.0])
    store.add(_record("retained:other", "decision", OTHER), [1.0, 1.0, 0.0])  # other scope
    return store


def _backup_cfg(tmp_path: Path, *, include_all: bool = False) -> BackupConfig:
    parser = argparse.ArgumentParser()
    add_backup_arguments(parser)
    argv = ["--repo", str(tmp_path), "--encoder", "deterministic", "--dim", "3",
            "--out", str(tmp_path / "backup.jsonl")]
    if include_all:
        argv.append("--all")
    return backup_config(parser.parse_args(argv))


def _restore_cfg(tmp_path: Path, *, dry_run: bool = False) -> RestoreConfig:
    parser = argparse.ArgumentParser()
    add_restore_arguments(parser)
    argv = ["--repo", str(tmp_path), "--encoder", "deterministic",
            "--src", str(tmp_path / "backup.jsonl")]
    if dry_run:
        argv.append("--dry-run")
    return restore_config(parser.parse_args(argv))


def test_backup_defaults_to_curated_only_and_scopes(tmp_path: Path) -> None:
    n = run_backup(_backup_cfg(tmp_path), store=_seed(_scope(tmp_path)))
    assert n == 2  # two retained: memories in scope; episode excluded, other scope excluded


def test_backup_all_includes_episodes(tmp_path: Path) -> None:
    n = run_backup(_backup_cfg(tmp_path, include_all=True), store=_seed(_scope(tmp_path)))
    assert n == 3  # retained x2 + episode, all in scope


def test_backup_then_restore_round_trips_with_embeddings(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    run_backup(_backup_cfg(tmp_path, include_all=True), store=_seed(scope))

    fresh = InMemoryStore(dim=3)
    assert run_restore(_restore_cfg(tmp_path), store=fresh) == 3

    by_id = {r.memory_id: (r, e) for r, e in fresh.scan_with_embeddings(scope)}
    assert set(by_id) == {MemoryId("retained:d1"), MemoryId("retained:g1"), MemoryId("episode:abc")}
    assert by_id[MemoryId("episode:abc")][1] == (0.0, 0.0, 1.0)  # embedding preserved
    record = by_id[MemoryId("retained:d1")][0]
    assert record.kind == "decision" and record.metadata["importance"] == 2.0


def test_restore_is_idempotent(tmp_path: Path) -> None:
    run_backup(_backup_cfg(tmp_path, include_all=True), store=_seed(_scope(tmp_path)))
    fresh = InMemoryStore(dim=3)
    run_restore(_restore_cfg(tmp_path), store=fresh)
    run_restore(_restore_cfg(tmp_path), store=fresh)  # re-run: upsert, no duplication
    assert len(fresh) == 3


def test_restore_dry_run_writes_nothing(tmp_path: Path) -> None:
    run_backup(_backup_cfg(tmp_path, include_all=True), store=_seed(_scope(tmp_path)))
    fresh = InMemoryStore(dim=3)
    assert run_restore(_restore_cfg(tmp_path, dry_run=True), store=fresh) == 3  # reported
    assert len(fresh) == 0  # but nothing written
