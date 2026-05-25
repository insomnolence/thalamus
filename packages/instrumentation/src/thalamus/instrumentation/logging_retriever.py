"""LoggingRetriever — a transparent decorator that emits the retrieval-event log.

Wraps any :class:`thalamus.core.Retriever`, records a :class:`RetrievalEvent`
per call, and returns the inner result unchanged. The inner retriever stays
pure (``core``-only); logging is swappable middleware behind the *same*
``Retriever`` protocol, so it can be added, removed, or pointed at a different
sink without touching callers.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from thalamus.core.protocols import Retriever
from thalamus.core.types import Cue, EventId, RetrievalResult
from thalamus.instrumentation.events import (
    CandidateLog,
    RetrievalEvent,
    ShownItem,
)
from thalamus.instrumentation.sinks import EventSink


def _uuid_event_id() -> EventId:
    return EventId(uuid.uuid4().hex)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class LoggingRetriever:
    """Decorate a retriever so every retrieval is logged to an :class:`EventSink`.

    Args:
        inner: The retriever whose calls are logged.
        sink: Where events are persisted.
        policy_id: Identifier of the inner policy (e.g. ``"L0"``) recorded per event.
        event_id_factory: Produces unique event ids (injectable for tests).
        now: Injectable clock (for deterministic tests).
    """

    def __init__(
        self,
        inner: Retriever,
        sink: EventSink,
        *,
        policy_id: str,
        event_id_factory: Callable[[], EventId] = _uuid_event_id,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._inner = inner
        self._sink = sink
        self._policy_id = policy_id
        self._event_id_factory = event_id_factory
        self._now = now

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        result = self._inner.retrieve(cue, k)
        event_id = self._event_id_factory()
        self._sink.emit(self._build_event(event_id, cue, k, result))
        # Stamp the event id onto the result so callers can correlate outcomes (§13.11).
        return replace(result, event_id=event_id)

    def _build_event(
        self, event_id: EventId, cue: Cue, k: int, result: RetrievalResult
    ) -> RetrievalEvent:
        candidates = [
            CandidateLog(memory_id=item.record.memory_id, features=dict(item.features))
            for item in result.candidates
        ]
        # Deterministic top-k: every shown item has propensity 1.0. Stochastic
        # rungs will surface real propensities (a future RetrievalResult field).
        shown = [
            ShownItem(memory_id=item.record.memory_id, rank=rank, propensity=1.0)
            for rank, item in enumerate(result.shown)
        ]
        return RetrievalEvent(
            event_id=event_id,
            timestamp=self._now(),
            scope=cue.scope,
            policy_id=self._policy_id,
            cue_text=cue.text,
            k_requested=k,
            candidates=candidates,
            shown=shown,
            session_id=cue.session_id,
            cue_focus=cue.focus,
            cue_intent=cue.intent,
            cue_embedding=cue.embedding,
        )
