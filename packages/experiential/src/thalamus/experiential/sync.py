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
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from thalamus.core.protocols import Encoder, Store
from thalamus.core.types import MemoryRecord, SessionId
from thalamus.experiential.episode import EpisodeBuilder
from thalamus.experiential.ingest import ingest_episodes
from thalamus.experiential.segmentation import EpisodeSegmenter
from thalamus.instrumentation import (
    SessionContextStore,
    TrajectoryEvent,
    TrajectoryEventKind,
    TrajectorySink,
)


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
        known_ids: set[str] | None = None,
    ) -> None:
        self._source = source
        self._encoder = encoder
        self._store = store
        self._checkpoint = checkpoint
        self._trajectory_sink = trajectory_sink
        self._raw_events = raw_events
        self._segmenter = segmenter
        self._builder = builder
        # Incremental mode: when a set of already-held episode ids is injected, sync embeds
        # only genuinely new spans (and keeps the set current as it ingests). A long-running
        # warm process — the serve's capture tick — seeds it from its startup scan so each
        # tick is O(new commits), not O(all episodes re-embedded). ``None`` keeps the original
        # re-embed-in-place behaviour (the one-shot CLI's full refresh).
        self._known_ids = known_ids

    def sync(self) -> list[MemoryRecord]:
        """Ingest commits since the checkpoint as episodes; advance the checkpoint.

        Idempotent and checkpoint-resumable. In incremental mode (``known_ids`` injected) a
        poll that finds no new commits short-circuits before touching the trajectory log, so
        an idle tick costs one ``git log`` and nothing else.
        """
        events = self._source.poll(self._checkpoint.load())
        incremental = self._known_ids is not None
        if not events and (self._raw_events is None or incremental):
            return []
        if self._trajectory_sink is not None:
            for event in events:
                self._trajectory_sink.emit(event)
        source_events = self._raw_events() if self._raw_events is not None else events
        known = self._known_ids
        records = ingest_episodes(
            source_events,
            encoder=self._encoder,
            store=self._store,
            segmenter=self._segmenter,
            builder=self._builder,
            skip_existing=(lambda episode_id: episode_id in known) if known is not None else None,
        )
        if known is not None:
            known.update(str(record.memory_id) for record in records)
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


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SessionStampingSource:
    """A :class:`CommitSource` decorator that stamps the active serve session id onto
    polled COMMIT events, so out-of-band commits join the session whose recalls informed
    them (the cue↔outcome join the proxy↔truth monitor needs, §13.12).

    The session is read from a :class:`SessionContextStore` (published by ``serve``). A
    commit is stamped only when its timestamp falls within the session window
    ``[started_at, now]`` and it is not already keyed; a commit from before the session
    began — or when no session is published — is left unkeyed: *missing over wrong*
    (§13.16). Composed in the composition root so ``GitObserver`` stays session-agnostic.

    Honest limit: one active session per repo. A commit made after a serve process exits
    while its context file lingers would attribute to that dead session; a freshness bound
    on ``last_recall_at`` is the documented later refinement.
    """

    def __init__(
        self,
        inner: CommitSource,
        sessions: SessionContextStore,
        *,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._inner = inner
        self._sessions = sessions
        self._now = now

    def poll(self, since: str | None = None) -> list[TrajectoryEvent]:
        events = self._inner.poll(since)
        ctx = self._sessions.read()
        if ctx is None:
            return events
        now = self._now()
        return [self._stamp(event, ctx.session_id, ctx.started_at, now) for event in events]

    @staticmethod
    def _stamp(
        event: TrajectoryEvent, session_id: SessionId, started_at: datetime, now: datetime
    ) -> TrajectoryEvent:
        if event.session_id is not None:
            return event  # already keyed (e.g. an explicit caller session)
        if started_at <= event.timestamp <= now:
            return replace(event, session_id=session_id)
        return event  # outside the session window — leave it missing, never mis-attribute
