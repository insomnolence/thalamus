"""``python -m thalamus.cli`` — the dogfood sync: a repo's commits → Brain 1, durably.

The *composition root* for the git→Brain 1 loop (``docs/deep-dives/path-to-real-data.md``):
the one place allowed to choose concretes (Neo4j vs. in-memory store, the encoder,
the git observer) and wire them into :class:`GitEpisodeIngestor`. Keeping this out of
the libraries is what lets them stay decoupled from concrete implementations (§14.5).
Run it after each commit and the project's own history accumulates as episodes.

Durable store: set ``THALAMUS_NEO4J_URI`` (+ ``_USER`` / ``_PASSWORD``) to persist
episodes in Neo4j across sessions; without it, it falls back to an in-memory store
with a loud warning (episodes will not survive the process). The checkpoint is a
file, so incremental sync resumes across runs.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from thalamus.core.protocols import Store
from thalamus.core.types import Hemisphere, MemoryRecord, RepoId, Scope, TenantId
from thalamus.experiential import FileCheckpoint, GitEpisodeIngestor
from thalamus.instrumentation import GitObserver
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore, Neo4jStore, connect

_DEFAULT_DIM = 128


@dataclass(frozen=True, slots=True)
class SyncConfig:
    repo: Path
    checkpoint: Path
    tenant: str
    repo_id: str
    dim: int
    neo4j_uri: str | None
    neo4j_user: str
    neo4j_password: str | None


def parse_args(argv: Sequence[str]) -> SyncConfig:
    parser = argparse.ArgumentParser(
        prog="python -m thalamus.cli",
        description="Sync a repository's commits into the experiential hemisphere (Brain 1).",
    )
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="repo to ingest (default: cwd)"
    )
    parser.add_argument(
        "--checkpoint", type=Path, default=None,
        help="checkpoint file (default: <repo>/.thalamus/checkpoints/git.cursor)",
    )
    parser.add_argument("--tenant", default="local", help="tenant id for scoping")
    parser.add_argument(
        "--repo-id", default=None, help="repo id for scoping (default: repo dir name)"
    )
    parser.add_argument("--dim", type=int, default=_DEFAULT_DIM, help="embedding dimensionality")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    checkpoint = (
        Path(args.checkpoint)
        if args.checkpoint is not None
        else repo / ".thalamus" / "checkpoints" / "git.cursor"
    )
    return SyncConfig(
        repo=repo,
        checkpoint=checkpoint,
        tenant=str(args.tenant),
        repo_id=str(args.repo_id) if args.repo_id else repo.name,
        dim=int(args.dim),
        neo4j_uri=os.environ.get("THALAMUS_NEO4J_URI"),
        neo4j_user=os.environ.get("THALAMUS_NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("THALAMUS_NEO4J_PASSWORD"),
    )


def _build_store(config: SyncConfig) -> Store:
    if config.neo4j_uri is not None:
        driver = connect(config.neo4j_uri, config.neo4j_user, config.neo4j_password or "")
        return Neo4jStore(dim=config.dim, driver=driver, hemisphere=Hemisphere.EXPERIENTIAL)
    print(
        "warning: THALAMUS_NEO4J_URI not set — using in-memory store; "
        "episodes will NOT persist across runs.",
        file=sys.stderr,
    )
    return InMemoryStore(dim=config.dim)


def build_ingestor(config: SyncConfig) -> tuple[GitEpisodeIngestor, Store]:
    """Wire the concrete encoder/store/observer/checkpoint into an ingestor."""
    encoder = DeterministicEncoder(dim=config.dim)
    store = _build_store(config)
    scope = Scope(tenant_id=TenantId(config.tenant), repo_id=RepoId(config.repo_id))
    observer = GitObserver(config.repo, scope)
    ingestor = GitEpisodeIngestor(
        observer, encoder=encoder, store=store, checkpoint=FileCheckpoint(config.checkpoint)
    )
    return ingestor, store


def run_sync(config: SyncConfig) -> list[MemoryRecord]:
    """Run one incremental sync, closing the store afterwards. Returns new episodes."""
    ingestor, store = build_ingestor(config)
    try:
        records = ingestor.sync()
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            close()
    print(f"synced {len(records)} new episode(s) into Brain 1 [{config.repo_id}]")
    for record in records:
        print(f"  {record.memory_id}  {record.content.splitlines()[0]}")
    return records


def main(argv: Sequence[str] | None = None) -> int:
    run_sync(parse_args(sys.argv[1:] if argv is None else argv))
    return 0
