"""thalamus.instrumentation — the logging contract (§13.11).

Three append-only signals, all joined by ``event_id``:
- the **retrieval-event log** (``LoggingRetriever`` → ``EventSink``): per-retrieval
  decision-time candidate features + shown/rank/propensity;
- the **episode-trajectory log** (observers → ``TrajectorySink``): out-of-band capture
  of commits/edits/tests (``GitObserver``, ``JUnitObserver``);
- **Tier-1 usage signals** (``attribute_overlap`` → ``UsageSink``): "was the surfaced
  memory used?" — the outcome that closes the loop.
"""

from collections.abc import Iterator
from pathlib import Path

from thalamus.instrumentation._jsonl import read_jsonl
from thalamus.instrumentation.events import (
    CandidateLog,
    RetrievalEvent,
    ShownItem,
    deserialize_event,
    serialize_event,
)
from thalamus.instrumentation.git_observer import GitObserver, reverted_shas
from thalamus.instrumentation.junit_observer import JUnitObserver
from thalamus.instrumentation.logging_retriever import LoggingRetriever
from thalamus.instrumentation.session import (
    FileSessionContextStore,
    SessionContext,
    SessionContextStore,
    default_session_path,
    deserialize_session_context,
    mint_session_id,
    serialize_session_context,
)
from thalamus.instrumentation.sinks import EventSink, InMemoryEventSink, JsonlEventSink
from thalamus.instrumentation.trajectory import (
    InMemoryTrajectorySink,
    JsonlTrajectorySink,
    TrajectoryEvent,
    TrajectoryEventKind,
    TrajectorySink,
    build_test_run_event,
    deserialize_trajectory_event,
    serialize_trajectory_event,
)
from thalamus.instrumentation.usage import (
    InMemoryUsageSink,
    JsonlUsageSink,
    UsageSignal,
    UsageSink,
    attribute_overlap,
    deserialize_usage,
    serialize_usage,
)


def read_event_log(path: Path) -> Iterator[RetrievalEvent]:
    """Stream a persisted retrieval-event log (JSONL) back into events."""
    return (deserialize_event(obj) for obj in read_jsonl(path))


def read_usage_log(path: Path) -> Iterator[UsageSignal]:
    """Stream a persisted Tier-1 usage log (JSONL) back into signals."""
    return (deserialize_usage(obj) for obj in read_jsonl(path))


def read_trajectory_log(path: Path) -> Iterator[TrajectoryEvent]:
    """Stream persisted raw trajectory observations back into events."""
    return (deserialize_trajectory_event(obj) for obj in read_jsonl(path))


__all__ = [
    "CandidateLog",
    "EventSink",
    "FileSessionContextStore",
    "GitObserver",
    "InMemoryEventSink",
    "InMemoryTrajectorySink",
    "InMemoryUsageSink",
    "JUnitObserver",
    "JsonlEventSink",
    "JsonlTrajectorySink",
    "JsonlUsageSink",
    "LoggingRetriever",
    "RetrievalEvent",
    "SessionContext",
    "SessionContextStore",
    "ShownItem",
    "TrajectoryEvent",
    "TrajectoryEventKind",
    "TrajectorySink",
    "UsageSignal",
    "UsageSink",
    "attribute_overlap",
    "build_test_run_event",
    "default_session_path",
    "deserialize_event",
    "deserialize_session_context",
    "deserialize_trajectory_event",
    "deserialize_usage",
    "mint_session_id",
    "read_event_log",
    "read_usage_log",
    "read_trajectory_log",
    "reverted_shas",
    "serialize_event",
    "serialize_session_context",
    "serialize_trajectory_event",
    "serialize_usage",
]
