"""Benchmark cases for retrieval evaluation.

A case is a cue plus the set of memory ids that *should* be surfaced. The curated
benchmark is the frozen regression guard (OLR §13.20); a JSONL loader keeps it
external and editable.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from thalamus.core.types import Cue, EventId, MemoryId, Scope
from thalamus.instrumentation import RetrievalEvent, UsageSignal


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    cue: Cue
    relevant: frozenset[MemoryId]


def cases_from_usage(
    events: Iterable[RetrievalEvent], signals: Iterable[UsageSignal]
) -> list[BenchmarkCase]:
    """Build benchmark cases from the live logs: each past recall's cue → the memories that were
    actually USED for it (the behavioral relevance label, joined by ``event_id``).

    This is the honest label for validating *re-ranking* rungs: not "is the top hit high-cosine"
    (saturated, and biased against re-rankers), but "did the rung rank the memory the actuator
    actually drew on higher." Pair with :func:`~thalamus.eval.compare` over the rung arms. ``used``
    comes from either the explicit ``record_usage`` log or the attribution log — both are
    :class:`UsageSignal`s, so pass whichever (or both). An event with no used memory yields no case.
    The cue is reconstructed faithfully (text + scope + focus + intent) so the re-run matches the
    original recall."""
    used_by_event: dict[EventId, set[MemoryId]] = {}
    for signal in signals:
        if signal.used:
            used_by_event.setdefault(signal.event_id, set()).add(signal.memory_id)
    cases: list[BenchmarkCase] = []
    for event in events:
        used = used_by_event.get(event.event_id)
        if not used:
            continue
        cue = Cue(
            text=event.cue_text, scope=event.scope,
            focus=event.cue_focus, intent=event.cue_intent,
        )
        cases.append(BenchmarkCase(cue=cue, relevant=frozenset(used)))
    return cases


def load_cases(path: Path, scope: Scope) -> list[BenchmarkCase]:
    """Load cases from newline-delimited JSON: ``{"query": ..., "relevant": [ids]}``."""
    cases: list[BenchmarkCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        obj = json.loads(stripped)
        relevant = frozenset(MemoryId(str(memory_id)) for memory_id in obj["relevant"])
        cases.append(BenchmarkCase(cue=Cue(text=str(obj["query"]), scope=scope), relevant=relevant))
    return cases
