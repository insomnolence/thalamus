"""The behavioral store — the brain's own durable record of how it has been used (Track I / B).

Today the usage-weighted recall rung's weights are *recomputed from the raw log files* every
maintenance tick: the brain reads its behavioral history out of loose JSONL on every cycle. Track I
moves that history *into* the brain. A :class:`BehavioralStore` is the durable, accumulating record
of which sessions each memory was recalled-and-used in; a dreaming consolidation pass folds new log
entries into it (:func:`consolidate_usage`), and the rung reads its weights from *here* instead of
from a file scan.

The accumulation is the key property: ``record_usage`` **unions** session sets, so consolidating the
same entries twice is a no-op, and — once the store is durable (the Neo4j impl) — re-consolidating a
*subset* of the logs after old segments are dropped neither double-counts nor loses signal. That is
what makes the raw logs a disposable write-ahead buffer rather than the system of record, and it
needs no cursor for correctness (a cursor is only a later efficiency optimization).

Firewall (§14.2/§14.3): the stored signal is a **behavioral act** — a session recalled and *used* a
memory — never the model grading its own memory prose. Consolidation is deterministic over immutable
logs, so it may *act* in a dreaming pass.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from typing import Protocol, runtime_checkable

from thalamus.core.types import MemoryId, SessionId
from thalamus.experiential.fate import usage_sessions_by_memory
from thalamus.instrumentation import RetrievalEvent, UsageSignal


@runtime_checkable
class BehavioralStore(Protocol):
    """Durable per-memory behavioral aggregate — the accumulating used-session sets per memory.

    Swappable (§14): an in-memory impl for tests/cold brains, a Neo4j impl for the live brain."""

    def record_usage(self, updates: Mapping[MemoryId, AbstractSet[SessionId]]) -> None:
        """Union each memory's newly-observed used-sessions into the durable record (idempotent)."""
        ...

    def usage_weights(self) -> dict[MemoryId, float]:
        """Per-memory usage weight = the count of distinct sessions it was recalled-and-used in —
        the same quantity the rung consumed from ``reuse_by_memory``, now read from the brain."""
        ...


class InMemoryBehavioralStore:
    """A dict-backed :class:`BehavioralStore` — the boring baseline (and the test/cold-brain impl).

    Not durable across a restart on its own: it re-accumulates from the logs each startup. The Neo4j
    impl (Track I increment 2) is what survives a restart and so makes the raw logs disposable."""

    def __init__(self) -> None:
        self._sessions: dict[MemoryId, set[SessionId]] = {}

    def record_usage(self, updates: Mapping[MemoryId, AbstractSet[SessionId]]) -> None:
        for memory_id, sessions in updates.items():
            self._sessions.setdefault(memory_id, set()).update(sessions)

    def usage_weights(self) -> dict[MemoryId, float]:
        return {memory_id: float(len(sessions)) for memory_id, sessions in self._sessions.items()}


def consolidate_usage(
    store: BehavioralStore,
    events: Iterable[RetrievalEvent],
    signals: Iterable[UsageSignal],
) -> int:
    """Fold the used-session sets from a slice of the logs into ``store`` (idempotent). Returns the
    number of memories that carried any used-session signal. The unit a consolidation pass runs."""
    updates = usage_sessions_by_memory(events, signals)
    store.record_usage(updates)
    return len(updates)
