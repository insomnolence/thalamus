"""``thalamus.cli backup`` / ``restore`` — a portable snapshot of durable Brain 1.

Incident insurance (a routine test run once wiped the dogfood store). Curated ``remember``
memories are **not** re-derivable — episodes rebuild from git via ``sync``, but
decisions/constraints/gotchas/investigations/preferences live only in the store. ``backup``
exports them **with their embedding vectors**, so a restore is faithful and
encoder-version-independent (curated memories embed a composed string that is not stored on
the record, so re-encoding could not reproduce them). ``restore`` re-adds records
idempotently (stable ids + the store's MERGE upsert). The snapshot is an artifact, never a
source of truth; the store stays authoritative. Default scope: curated kinds only (``--all``
includes episodes, which are re-derivable anyway).
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thalamus.cli.brain import build_store, close_store
from thalamus.core.exceptions import ThalamusError
from thalamus.core.protocols import EmbeddingStore, Encoder, Store
from thalamus.core.serde import deserialize_memory_record, serialize_memory_record
from thalamus.core.types import Hemisphere, RepoId, Scope, TenantId, Vector
from thalamus.routing import BgeEncoder, DeterministicEncoder

_CURATED_KINDS = frozenset({"decision", "constraint", "gotcha", "investigation", "preference"})
_DEFAULT_DIM = 128
_DEFAULT_ENCODER = "bge-small"


@dataclass(frozen=True, slots=True)
class BackupConfig:
    repo: Path
    tenant: str
    repo_id: str
    dim: int
    encoder: str
    out: Path
    include_all: bool  # --all: include episodes (default: curated kinds only)
    neo4j_uri: str | None
    neo4j_user: str
    neo4j_password: str | None


def _build_encoder(name: str, dim: int) -> Encoder:
    if name == "bge-small":
        return BgeEncoder("BAAI/bge-small-en-v1.5")
    return DeterministicEncoder(dim=dim)


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:  # overwrite: a snapshot, not append-only
        for row in rows:
            handle.write(json.dumps(row) + "\n")
            count += 1
    return count


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def add_backup_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repo whose scope to back up")
    parser.add_argument("--tenant", default="local", help="tenant id")
    parser.add_argument("--repo-id", default=None, help="repo id (default: repo dir name)")
    parser.add_argument("--dim", type=int, default=_DEFAULT_DIM, help="embedding dim (store)")
    parser.add_argument(
        "--encoder", choices=("bge-small", "deterministic"), default=_DEFAULT_ENCODER,
        help="encoder identity for the store (sizes it; no re-encoding happens)",
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="output JSONL (default: .thalamus/backups/)"
    )
    parser.add_argument(
        "--all", dest="include_all", action="store_true",
        help="also back up episodes (default: curated memories only — episodes rebuild via sync)",
    )


def backup_config(args: argparse.Namespace) -> BackupConfig:
    repo = Path(args.repo).resolve()
    repo_id = str(args.repo_id) if args.repo_id else repo.name
    default_out = repo / ".thalamus" / "backups" / f"brain1-{repo_id}.jsonl"
    out = Path(args.out) if args.out else default_out
    return BackupConfig(
        repo=repo, tenant=str(args.tenant), repo_id=repo_id, dim=int(args.dim),
        encoder=str(args.encoder), out=out, include_all=bool(args.include_all),
        neo4j_uri=os.environ.get("THALAMUS_NEO4J_URI"),
        neo4j_user=os.environ.get("THALAMUS_NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("THALAMUS_NEO4J_PASSWORD"),
    )


def run_backup(
    config: BackupConfig, *, store: Store | None = None, encoder: Encoder | None = None
) -> int:
    """Export the scope's durable memories (with embeddings) to a JSONL snapshot."""
    encoder = encoder or _build_encoder(config.encoder, config.dim)
    own_store = store is None
    if store is None:  # an empty injected store is falsy (__len__), so check None explicitly
        store = build_store(
            dim=encoder.dim, neo4j_uri=config.neo4j_uri, neo4j_user=config.neo4j_user,
            neo4j_password=config.neo4j_password, encoder_id=config.encoder,
        )
    try:
        if not isinstance(store, EmbeddingStore):
            raise ThalamusError("store cannot export embeddings (needs scan_with_embeddings)")
        scope = Scope(tenant_id=TenantId(config.tenant), repo_id=RepoId(config.repo_id))
        rows = (
            {"record": serialize_memory_record(record), "embedding": [float(v) for v in embedding]}
            for record, embedding in store.scan_with_embeddings(scope)
            if config.include_all or record.kind in _CURATED_KINDS
        )
        written = _write_jsonl(config.out, rows)
    finally:
        if own_store:
            close_store(store)
    print(f"backed up {written} record(s) -> {config.out}")
    return written


@dataclass(frozen=True, slots=True)
class RestoreConfig:
    repo: Path
    encoder: str
    src: Path
    dry_run: bool
    neo4j_uri: str | None
    neo4j_user: str
    neo4j_password: str | None


def add_restore_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="repo (for the default backup path)"
    )
    parser.add_argument(
        "--encoder", choices=("bge-small", "deterministic"), default=_DEFAULT_ENCODER,
        help="encoder identity for the store (must match how it was created)",
    )
    parser.add_argument(
        "--src", type=Path, default=None, help="backup JSONL (default: .thalamus/backups/)"
    )
    parser.add_argument("--dry-run", action="store_true", help="report counts without writing")


def restore_config(args: argparse.Namespace) -> RestoreConfig:
    repo = Path(args.repo).resolve()
    default_src = repo / ".thalamus" / "backups" / f"brain1-{repo.name}.jsonl"
    src = Path(args.src) if args.src else default_src
    return RestoreConfig(
        repo=repo, encoder=str(args.encoder), src=src, dry_run=bool(args.dry_run),
        neo4j_uri=os.environ.get("THALAMUS_NEO4J_URI"),
        neo4j_user=os.environ.get("THALAMUS_NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("THALAMUS_NEO4J_PASSWORD"),
    )


def run_restore(config: RestoreConfig, *, store: Store | None = None) -> int:
    """Re-add records (with their stored embeddings) from a snapshot. Idempotent (upsert)."""
    rows = list(_read_jsonl(config.src))
    if config.dry_run or not rows:
        print(f"restore: {len(rows)} record(s) in {config.src} (dry-run={config.dry_run})")
        return len(rows)
    # Store dim must match the snapshot's embedding length; infer it (no encoder needed).
    dim = len(rows[0]["embedding"])
    own_store = store is None
    if store is None:  # an empty injected store is falsy (__len__), so check None explicitly
        store = build_store(
            dim=dim, neo4j_uri=config.neo4j_uri, neo4j_user=config.neo4j_user,
            neo4j_password=config.neo4j_password, hemisphere=Hemisphere.EXPERIENTIAL,
            encoder_id=config.encoder,
        )
    try:
        for row in rows:
            record = deserialize_memory_record(row["record"])
            embedding: Vector = [float(value) for value in row["embedding"]]
            store.add(record, embedding)
    finally:
        if own_store:
            close_store(store)
    print(f"restored {len(rows)} record(s) from {config.src}")
    return len(rows)
