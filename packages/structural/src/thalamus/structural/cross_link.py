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

from thalamus.core.types import MemoryId


@runtime_checkable
class CrossLinkIndex(Protocol):
    """Bidirectional index of experiential-memory ↔ structural-node links."""

    def link(self, memory_id: MemoryId, node_id: str) -> None: ...
    def nodes_for(self, memory_id: MemoryId) -> list[str]: ...
    def memories_for(self, node_id: str) -> list[MemoryId]: ...


class InMemoryCrossLinkIndex:
    """In-memory cross-link index (insertion-ordered, deduplicated)."""

    def __init__(self) -> None:
        self._by_memory: dict[MemoryId, list[str]] = {}
        self._by_node: dict[str, list[MemoryId]] = {}

    def link(self, memory_id: MemoryId, node_id: str) -> None:
        nodes = self._by_memory.setdefault(memory_id, [])
        if node_id not in nodes:
            nodes.append(node_id)
        memories = self._by_node.setdefault(node_id, [])
        if memory_id not in memories:
            memories.append(memory_id)

    def nodes_for(self, memory_id: MemoryId) -> list[str]:
        return list(self._by_memory.get(memory_id, []))

    def memories_for(self, node_id: str) -> list[MemoryId]:
        return list(self._by_node.get(node_id, []))
