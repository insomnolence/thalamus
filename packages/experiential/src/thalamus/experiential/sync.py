"""Incremental ingestion — poll a commit source into Brain 1, checkpointed.

Turns the one-shot spine (:func:`ingest_episodes`) into a re-runnable sync: poll a
repo for commits since the last checkpoint, materialize them as episodes, and
advance the checkpoint. This is the dogfood loop (``docs/deep-dives/path-to-real-data.md``)
— run it after each commit and the project's own history becomes Brain-1 episodes.

The checkpoint is an *efficiency* cursor, not a correctness requirement: episode ids
are stable, so re-ingesting an already-seen commit is idempotent (§14.1). The cursor
advances to the newest commit actually ingested, so a commit is never *skipped* (the
worst case is a harmless re-ingest). The source and checkpoint are both behind
protocols, so neither ``GitObserver`` nor on-disk storage is baked in (§14.5).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from thalamus.core.protocols import Encoder, Store
from thalamus.core.types import MemoryRecord
from thalamus.experiential.episode import EpisodeBuilder
from thalamus.experiential.ingest import ingest_episodes
from thalamus.experiential.segmentation import EpisodeSegmenter
from thalamus.instrumentation import TrajectoryEvent, TrajectoryEventKind, TrajectorySink


@runtime_checkable
class CommitSource(Protocol):
    """Yields commit trajectory events since a cursor sha (e.g. ``GitObserver``)."""

    def poll(self, since: str | None = None) -> list[TrajectoryEvent]:
        """Return COMMIT events for commits after ``since`` (or all if ``None``)."""
        ...


@runtime_checkable
class Checkpoint(Protocol):
    """Persists a single ingestion cursor (the last-ingested commit sha)."""

    def load(self) -> str | None: ...
    def save(self, cursor: str) -> None: ...


class InMemoryCheckpoint:
    """In-process cursor. For tests and ephemeral runs."""

    def __init__(self, cursor: str | None = None) -> None:
        self._cursor = cursor

    def load(self) -> str | None:
        return self._cursor

    def save(self, cursor: str) -> None:
        self._cursor = cursor


class FileCheckpoint:
    """Cursor persisted as a single line in a file (survives process restarts)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> str | None:
        try:
            return self._path.read_text(encoding="utf-8").strip() or None
        except FileNotFoundError:
            return None

    def save(self, cursor: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(cursor, encoding="utf-8")


class GitEpisodeIngestor:
    """Polls a :class:`CommitSource` into Brain 1, checkpointed and idempotent."""

    def __init__(
        self,
        source: CommitSource,
        *,
        encoder: Encoder,
        store: Store,
        checkpoint: Checkpoint,
        trajectory_sink: TrajectorySink | None = None,
        raw_events: Callable[[], Sequence[TrajectoryEvent]] | None = None,
        segmenter: EpisodeSegmenter | None = None,
        builder: EpisodeBuilder | None = None,
    ) -> None:
        self._source = source
        self._encoder = encoder
        self._store = store
        self._checkpoint = checkpoint
        self._trajectory_sink = trajectory_sink
        self._raw_events = raw_events
        self._segmenter = segmenter
        self._builder = builder

    def sync(self) -> list[MemoryRecord]:
        """Ingest commits since the checkpoint as episodes; advance the checkpoint."""
        events = self._source.poll(self._checkpoint.load())
        if not events and self._raw_events is None:
            return []
        if self._trajectory_sink is not None:
            for event in events:
                self._trajectory_sink.emit(event)
        source_events = self._raw_events() if self._raw_events is not None else events
        records = ingest_episodes(
            source_events,
            encoder=self._encoder,
            store=self._store,
            segmenter=self._segmenter,
            builder=self._builder,
        )
        cursor = self._newest_commit_sha(events)
        if cursor is not None:
            self._checkpoint.save(cursor)
        return records

    @staticmethod
    def _newest_commit_sha(events: list[TrajectoryEvent]) -> str | None:
        for event in reversed(events):
            if event.kind is TrajectoryEventKind.COMMIT:
                return str(event.payload["sha"])
        return None
