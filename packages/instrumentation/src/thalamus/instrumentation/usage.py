"""Tier-1 usage signals — closing the loop from "we surfaced this" to "was it used".

:class:`UsageSignal` is the shared Tier-1 unit (joined to the retrieval-event log by
``event_id``, the numerator of ``utility@k``). It carries a ``kind`` naming the signal's
source, ordered by the §13.8 taxonomy:

- **primary, deterministic:** structural footprint overlap between a memory's footprint
  and the work's footprint (``kind="footprint"``/``"footprint-khop"``) — produced by
  ``thalamus.structural.FootprintAttributor`` (it needs the structural graph, so it lives
  there; this module only holds the shared unit + sinks).
- **secondary, cooperation-dependent:** :func:`attribute_overlap` — does the actuator's
  ``record_usage`` output overlap a surfaced memory's content? This is the §13.8 *citation*
  tier (``kind="citation"``): an actuator self-report, useful but not the training target.

Lexical output-overlap was the v0 *primary* signal; it under-counted real semantic use
(genuine use rarely re-quotes a memory verbatim), so the deterministic primary moved to
structural footprint attribution and this dropped to the citation tier (OLR §13.8/§13.10).
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
    kind: str  # signal source: "footprint"/"footprint-khop" (primary); "citation" (secondary)
    value: float  # signal strength in [0, 1]
    used: bool


class AttributedSignalsRef:
    """Single-slot, atomic-swap holder of the latest footprint-attribution signals.

    Mirrors ``retrieval.UsageWeightsRef``/``structural.CoChangeRef``: the footprint usage signals
    are a *re-derivable view* of the raw logs + the current code graph, so a long-running serve must
    refresh them mid-flight (the ``AttributionRefreshPass`` recomputes and swaps them in each
    maintenance tick) rather than read a file written once at startup. ``refresh`` replaces the slot
    atomically under the GIL (a concurrent reader observes the old or new tuple whole, never a
    partial); a consumer snapshots ``signals`` once per use. Holding the signals in memory lets the
    live usage rung read fresh attribution without a file round-trip; the pass still writes the
    derived log for the offline ``verdict``/``rung-eval`` tools."""

    def __init__(self) -> None:
        self._signals: tuple[UsageSignal, ...] = ()

    @property
    def signals(self) -> tuple[UsageSignal, ...]:
        return self._signals

    def refresh(self, signals: Sequence[UsageSignal]) -> None:
        self._signals = tuple(signals)


def attribute_overlap(
    event_id: EventId,
    shown: Sequence[tuple[MemoryId, str]],
    output: str,
    *,
    threshold: float = 0.5,
) -> list[UsageSignal]:
    """The **secondary citation** signal (§13.8): per shown memory, the fraction of its
    content tokens that appear in the actuator's ``output``; ``used`` when ≥ ``threshold``.

    Emits ``kind="citation"`` — it is the actuator's self-report (cooperation-dependent),
    not the deterministic primary signal (that is structural footprint attribution). Useful
    enrichment that degrades to nothing when the actuator never calls ``record_usage``."""
    out_tokens = _tokens(output)
    signals: list[UsageSignal] = []
    for memory_id, content in shown:
        mem_tokens = _tokens(content)
        value = len(mem_tokens & out_tokens) / len(mem_tokens) if mem_tokens else 0.0
        signals.append(UsageSignal(event_id, memory_id, "citation", value, value >= threshold))
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
