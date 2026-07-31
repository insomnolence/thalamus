"""``thalamus.cli sync`` — the dogfood sync: a repo's commits → Brain 1, durably.

The *composition root* for the git→Brain 1 loop (``docs/deep-dives/path-to-real-data.md``):
the one place allowed to choose concretes (Neo4j vs. in-memory store, the encoder,
the git observer) and wire them into :class:`GitEpisodeIngestor`. Keeping this out of
the libraries is what lets them stay decoupled from concrete implementations (§14.5).
Run it after each commit and the project's own history accumulates as episodes.

Durable store: set ``THALAMUS_NEO4J_URI`` (+ ``_USER`` / ``_PASSWORD``) to persist
episodes in Neo4j across sessions; without it, it falls back to an in-memory store
with a loud warning. The checkpoint is a file, so incremental sync resumes across runs.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from thalamus.cli.brain import build_store, close_store
from thalamus.core.protocols import Store
from thalamus.core.types import MemoryRecord, RepoId, Scope, TenantId
from thalamus.experiential import FileCheckpoint, GitEpisodeIngestor, SessionStampingSource
from thalamus.instrumentation import (
    FileSessionContextStore,
    GitObserver,
    JsonlTrajectorySink,
    default_session_path,
    read_trajectory_log,
)
from thalamus.routing import ENCODER_NAMES, build_encoder

_DEFAULT_DIM = 128
_DEFAULT_ENCODER = "bge-small"


@dataclass(frozen=True, slots=True)
class SyncConfig:
    repo: Path
    checkpoint: Path
    tenant: str
    repo_id: str
    dim: int
    encoder: str
    neo4j_uri: str | None
    neo4j_user: str
    neo4j_password: str | None
    # Where the brain's .thalamus data (checkpoint, trajectory log, session) lives. Defaults to
    # --repo; set it so syncing a code repo's commits doesn't write .thalamus into that repo
    # (and so it shares the SAME data dir as the matching `serve --data-dir`).
    data_dir: Path | None = None


def add_sync_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="repo to ingest (default: cwd)"
    )
    parser.add_argument(
        "--checkpoint", type=Path, default=None,
        help="checkpoint file (default: <data-dir>/.thalamus/checkpoints/git.cursor)",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=None,
        help="directory under which the brain's .thalamus data (checkpoint/trajectory/session) "
        "lives (default: --repo). Set it to keep .thalamus out of the code repo and to share the "
        "same data dir as `serve --data-dir`.",
    )
    parser.add_argument("--tenant", default="local", help="tenant id for scoping")
    parser.add_argument(
        "--repo-id", default=None, help="repo id for scoping (default: repo dir name)"
    )
    parser.add_argument("--dim", type=int, default=_DEFAULT_DIM, help="embedding dimensionality")
    parser.add_argument(
        "--encoder", choices=ENCODER_NAMES, default=_DEFAULT_ENCODER,
        help="embedding model (default: bge-small; deterministic is for smoke tests)",
    )


def sync_config(args: argparse.Namespace) -> SyncConfig:
    repo = Path(args.repo).resolve()
    data_dir = Path(args.data_dir).resolve() if args.data_dir else repo
    checkpoint = (
        Path(args.checkpoint)
        if args.checkpoint is not None
        else data_dir / ".thalamus" / "checkpoints" / "git.cursor"
    )
    return SyncConfig(
        repo=repo,
        checkpoint=checkpoint,
        tenant=str(args.tenant),
        repo_id=str(args.repo_id) if args.repo_id else repo.name,
        dim=int(args.dim),
        encoder=str(args.encoder),
        neo4j_uri=os.environ.get("THALAMUS_NEO4J_URI"),
        neo4j_user=os.environ.get("THALAMUS_NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("THALAMUS_NEO4J_PASSWORD"),
        data_dir=data_dir,
    )


def parse_args(argv: Sequence[str]) -> SyncConfig:
    """Standalone parse of the sync arguments (the subcommand dispatcher reuses the pieces)."""
    parser = argparse.ArgumentParser(
        prog="python -m thalamus.cli sync",
        description="Sync a repository's commits into the experiential hemisphere (Brain 1).",
    )
    add_sync_arguments(parser)
    return sync_config(parser.parse_args(argv))


def build_ingestor(config: SyncConfig) -> tuple[GitEpisodeIngestor, Store]:
    """Wire the concrete encoder/store/observer/checkpoint into an ingestor."""
    encoder = build_encoder(config.encoder, dim=config.dim)
    store = build_store(
        dim=encoder.dim,
        neo4j_uri=config.neo4j_uri,
        neo4j_user=config.neo4j_user,
        neo4j_password=config.neo4j_password,
        encoder_id=config.encoder,
    )
    scope = Scope(tenant_id=TenantId(config.tenant), repo_id=RepoId(config.repo_id))
    data_dir = config.data_dir or config.repo  # brain data home — may differ from the code repo
    # Stamp commits with the active serve session so they join that session's recalls
    # (the cue↔outcome join); GitObserver itself stays session-agnostic. The session file is
    # under the brain's data dir (shared with `serve --data-dir`), not the code repo.
    source = SessionStampingSource(
        GitObserver(config.repo, scope),
        FileSessionContextStore(default_session_path(data_dir)),
    )
    trajectory_path = data_dir / ".thalamus" / "logs" / "trajectory.jsonl"
    ingestor = GitEpisodeIngestor(
        source,
        encoder=encoder,
        store=store,
        checkpoint=FileCheckpoint(config.checkpoint),
        trajectory_sink=JsonlTrajectorySink(trajectory_path),
        raw_events=lambda: list(read_trajectory_log(trajectory_path)),
    )
    return ingestor, store


def run_sync(config: SyncConfig) -> list[MemoryRecord]:
    """Run one incremental sync, closing the store afterwards. Returns new episodes."""
    ingestor, store = build_ingestor(config)
    try:
        records = ingestor.sync()
    finally:
        close_store(store)
    print(f"materialized {len(records)} episode(s) into Brain 1 [{config.repo_id}]")
    for record in records:
        print(f"  {record.memory_id}  {record.content.splitlines()[0]}")
    return records
