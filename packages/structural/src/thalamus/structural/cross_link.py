"""Cross-hemisphere links: experiential memory ↔ structural node (§13.19).

The connective tissue — an experiential memory (*why* we did X) anchored to the
structural node where X lives (a code symbol; later, a doc section). v0 is an
explicit in-memory index. Deferred (deep-dives/structural-hemisphere.md):
deterministic auto-linking from an episode's trajectory footprint, and
symbol-identity re-resolution. A link whose node is absent from the current graph
is **stale** — which doubles as the §13.18-D2 staleness signal.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from thalamus.core.types import MemoryRef, StructuralRef


@runtime_checkable
class CrossLinkIndex(Protocol):
    """Bidirectional index of experiential-memory ↔ structural-node links."""

    def link(self, memory: MemoryRef, node: StructuralRef) -> None: ...
    def nodes_for(self, memory: MemoryRef) -> list[StructuralRef]: ...
    def memories_for(self, node: StructuralRef) -> list[MemoryRef]: ...


class InMemoryCrossLinkIndex:
    """In-memory cross-link index (insertion-ordered, deduplicated)."""

    def __init__(self) -> None:
        self._by_memory: dict[MemoryRef, list[StructuralRef]] = {}
        self._by_node: dict[StructuralRef, list[MemoryRef]] = {}

    def link(self, memory: MemoryRef, node: StructuralRef) -> None:
        if memory.scope != node.scope:
            raise ValueError("cross-link endpoints must have the same scope")
        nodes = self._by_memory.setdefault(memory, [])
        if node not in nodes:
            nodes.append(node)
        memories = self._by_node.setdefault(node, [])
        if memory not in memories:
            memories.append(memory)

    def nodes_for(self, memory: MemoryRef) -> list[StructuralRef]:
        return list(self._by_memory.get(memory, []))

    def memories_for(self, node: StructuralRef) -> list[MemoryRef]:
        return list(self._by_node.get(node, []))
