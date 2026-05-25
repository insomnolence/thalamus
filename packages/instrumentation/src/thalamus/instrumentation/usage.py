"""Tier-1 usage signals — closing the loop from "we surfaced this" to "was it used".

The deterministic, *primary* Tier-1 signal (OLR §13.8 / §13.10): does the actuator's
output overlap a surfaced memory's content? Joined to the retrieval-event log by
``event_id``. This is the training target for outcome-learned retrieval and the
numerator of `utility@k` in the eval. Citation (actuator self-report) is a secondary,
cooperation-dependent signal added later. Overlap is a coarse v0 proxy; symbol-level
overlap and constraint-honored checks refine it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from thalamus.core.types import EventId, MemoryId
from thalamus.instrumentation._jsonl import append_jsonl

_WORD = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_STOP = frozenset(
    {
        "the", "a", "an", "to", "for", "of", "in", "on", "and", "or", "is", "are",
        "be", "this", "that", "it", "we", "you", "do", "does", "use", "used",
        "using", "with", "as", "at", "by", "from", "return",
    }
)


def _tokens(text: str) -> set[str]:
    return {token for token in (w.lower() for w in _WORD.findall(text)) if token not in _STOP}


@dataclass(frozen=True, slots=True)
class UsageSignal:
    """A Tier-1 usage signal for one surfaced memory, joined by ``event_id``."""

    event_id: EventId
    memory_id: MemoryId
    kind: str  # "overlap" (deterministic Tier-1); "citation" / "constraint-honored" later
    value: float  # overlap ratio in [0, 1]
    used: bool


def attribute_overlap(
    event_id: EventId,
    shown: Sequence[tuple[MemoryId, str]],
    output: str,
    *,
    threshold: float = 0.5,
) -> list[UsageSignal]:
    """Per shown memory, the fraction of its content tokens that appear in ``output``;
    ``used`` when that fraction ≥ ``threshold``. Deterministic and content-based."""
    out_tokens = _tokens(output)
    signals: list[UsageSignal] = []
    for memory_id, content in shown:
        mem_tokens = _tokens(content)
        value = len(mem_tokens & out_tokens) / len(mem_tokens) if mem_tokens else 0.0
        signals.append(UsageSignal(event_id, memory_id, "overlap", value, value >= threshold))
    return signals


def serialize_usage(signal: UsageSignal) -> dict[str, Any]:
    return {
        "event_id": str(signal.event_id),
        "memory_id": str(signal.memory_id),
        "kind": signal.kind,
        "value": signal.value,
        "used": signal.used,
    }


def deserialize_usage(obj: Mapping[str, Any]) -> UsageSignal:
    """Reconstruct a :class:`UsageSignal` from :func:`serialize_usage`'s output."""
    return UsageSignal(
        event_id=EventId(str(obj["event_id"])),
        memory_id=MemoryId(str(obj["memory_id"])),
        kind=str(obj["kind"]),
        value=float(obj["value"]),
        used=bool(obj["used"]),
    )


@runtime_checkable
class UsageSink(Protocol):
    """Persists Tier-1 usage signals. Append-only."""

    def emit(self, signal: UsageSignal) -> None: ...


class InMemoryUsageSink:
    """Collects usage signals in a list. For tests and short-lived analysis."""

    def __init__(self) -> None:
        self.signals: list[UsageSignal] = []

    def emit(self, signal: UsageSignal) -> None:
        self.signals.append(signal)


class JsonlUsageSink:
    """Appends one JSON usage signal per line."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def emit(self, signal: UsageSignal) -> None:
        append_jsonl(self._path, serialize_usage(signal))
