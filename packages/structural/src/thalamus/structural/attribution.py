"""Deterministic usage attribution by structural footprint overlap (§13.10 rung b, §13.19).

The Tier-1 "was this recalled memory actually used?" signal, computed the design's way.
**Not** lexical text overlap (too crude) and **not** an embedding distance — the §13.10
*distillation-collapse trap*: a BGE-dominated signal merely reproduces BGE aboutness and the
learned layer learns nothing, because "the valuable positives are deterministically-connected
but BGE-distant." Instead: **structural overlap** between the code a memory is *about* (its
footprint) and the code the work actually *touched* (the session's commit footprint), via the
same module index + graph k-hop that :func:`~thalamus.structural.linking.link_by_footprint`
uses — the cross-hemisphere link (§13.19) doing credit assignment.

A memory is ``used`` when its footprint nodes intersect the work's footprint nodes (direct,
``connection="footprint"``) or their k-hop neighbourhood (``"footprint-khop"``). The tier is
recorded, not collapsed, so the §13.10 tiered-confidence weighting can use it later. The signal
is an external act (files / graph), never a retriever-space distance, so the FM1 self-reference
invariant holds (CLAUDE.md §2). Pure and deterministic; the composition root turns the results
into Tier-1 ``UsageSignal``s, and the §13.10 hindsight-relabeling pass consumes the same join.

**Limit:** code-agnostic memories (no footprint) cannot be attributed by this signal — that is
the secondary *citation* signal's job (the actuator's explicit ``record_usage``), not this one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from thalamus.core.types import EventId, MemoryId, StructuralRef
from thalamus.structural.graph import StructuralGraph
from thalamus.structural.linking import module_index
from thalamus.structural.schema import StructuralNode


@dataclass(frozen=True, slots=True)
class ShownMemory:
    """A memory surfaced by a recall, with the repo-relative files it is about."""

    memory_id: MemoryId
    footprint: Sequence[str]


@dataclass(frozen=True, slots=True)
class AttributedUse:
    """The deterministic usage verdict for one ``(recall event, surfaced memory)`` pair."""

    event_id: EventId
    memory_id: MemoryId
    used: bool
    value: float  # fraction of the memory's footprint nodes connected to the work, in [0, 1]
    connection: str  # "footprint" (direct) | "footprint-khop" | "none" — the §13.10 tier


@runtime_checkable
class UsageAttributor(Protocol):
    """Deterministic Tier-1 usage attribution: which surfaced memories the work used.

    ``recalls`` are one session's recall events (each its shown memories + footprints);
    ``work_files`` are the repo-relative files that session's work touched. Swappable
    (§14): a symbol-level attributor (the §13.10 "gold" rung) slots in behind this seam."""

    def attribute(
        self,
        recalls: Iterable[tuple[EventId, Sequence[ShownMemory]]],
        work_files: Iterable[str],
    ) -> list[AttributedUse]: ...


class FootprintAttributor:
    """Attribute usage by structural footprint overlap with a session's work.

    Built once over the structural graph's nodes (for the module index) + the graph (for
    k-hop expansion); :meth:`attribute` is then called once per session with that session's
    recalls and work footprint. ``k_hop=0`` restricts to direct (same-module) overlap."""

    def __init__(
        self,
        graph: StructuralGraph,
        nodes: Iterable[StructuralNode],
        *,
        repo_root: Path,
        k_hop: int = 1,
    ) -> None:
        self._graph = graph
        self._index = module_index(nodes, repo_root)
        self._k_hop = k_hop

    def _refs(self, files: Iterable[str]) -> set[StructuralRef]:
        return {ref for file in files if (ref := self._index.get(file)) is not None}

    def _neighbourhood(self, refs: set[StructuralRef]) -> set[StructuralRef]:
        expanded = set(refs)
        if self._k_hop > 0:
            for ref in refs:
                expanded.update(node.ref for node in self._graph.k_hop(ref, self._k_hop))
        return expanded

    def attribute(
        self,
        recalls: Iterable[tuple[EventId, Sequence[ShownMemory]]],
        work_files: Iterable[str],
    ) -> list[AttributedUse]:
        work_refs = self._refs(work_files)
        neighbourhood = self._neighbourhood(work_refs)
        results: list[AttributedUse] = []
        for event_id, shown in recalls:
            for memory in shown:
                memory_refs = self._refs(memory.footprint)
                connected = memory_refs & neighbourhood
                if memory_refs & work_refs:
                    connection = "footprint"
                elif connected:
                    connection = "footprint-khop"
                else:
                    connection = "none"
                value = len(connected) / len(memory_refs) if memory_refs else 0.0
                results.append(
                    AttributedUse(event_id, memory.memory_id, bool(connected), value, connection)
                )
        return results
