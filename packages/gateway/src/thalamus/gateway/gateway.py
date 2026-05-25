"""The Gateway — the single conduit the actuator talks to.

Cue in, assembled ``ContextPayload`` out. Depends on the ``core.Retriever``
protocol for experiential recall, and *optionally* on a structural graph +
cross-hemisphere link index (Brain 2) to enrich the payload with related code
(§13.19). The MCP transport is a thin adapter on top (``server.py``); this class
is pure and testable. Without the structural collaborators it works experiential-only.
"""

from __future__ import annotations

from collections.abc import Sequence

from thalamus.core.protocols import Retriever
from thalamus.core.types import Cue, MemoryId, Scope, SessionId
from thalamus.gateway.payload import ContextPayload, MemoryItem, StructuralItem
from thalamus.instrumentation import UsageSignal, UsageSink, attribute_overlap
from thalamus.structural import CrossLinkIndex, StructuralGraph, StructuralNode


class Gateway:
    """Assembles context for a cue by querying the brain through a retriever."""

    def __init__(
        self,
        retriever: Retriever,
        *,
        k: int = 5,
        graph: StructuralGraph | None = None,
        links: CrossLinkIndex | None = None,
        structural_k_hop: int = 0,
        usage_sink: UsageSink | None = None,
    ) -> None:
        self._retriever = retriever
        self._k = k
        self._graph = graph
        self._links = links
        self._structural_k_hop = structural_k_hop
        self._usage_sink = usage_sink

    def recall(
        self,
        *,
        prompt: str,
        scope: Scope,
        focus: str | None = None,
        session_id: SessionId | None = None,
    ) -> ContextPayload:
        cue = Cue(text=prompt, scope=scope, focus=focus, session_id=session_id)
        result = self._retriever.retrieve(cue, self._k)
        memories = [MemoryItem.from_scored(scored) for scored in result.shown]
        structural = self._structural_for([scored.record.memory_id for scored in result.shown])
        return ContextPayload(
            cue_text=prompt, memories=memories, structural=structural, event_id=result.event_id
        )

    def _structural_for(self, memory_ids: Sequence[MemoryId]) -> list[StructuralItem]:
        graph, links = self._graph, self._links
        if graph is None or links is None:
            return []
        seen: set[str] = set()
        items: list[StructuralItem] = []
        for memory_id in memory_ids:
            for node_id in links.nodes_for(memory_id):
                for node in self._resolve(graph, node_id):
                    if node.node_id not in seen:
                        seen.add(node.node_id)
                        items.append(StructuralItem.from_node(node))
        return items

    def _resolve(self, graph: StructuralGraph, node_id: str) -> list[StructuralNode]:
        node = graph.get(node_id)
        if node is None:
            # Stale link: the anchored node is gone from the current graph — the
            # §13.18-D2 staleness signal. Skipped for now; flagged/surfaced later.
            return []
        return [node, *graph.k_hop(node_id, self._structural_k_hop)]

    def record_outcome(self, payload: ContextPayload, output: str) -> list[UsageSignal]:
        """Attribute Tier-1 usage: which surfaced memories the actuator's ``output`` used.

        Stateless — pass back the payload from :meth:`recall`. Signals are logged to the
        usage sink keyed by the payload's ``event_id`` (the loop close: surfaced → used).
        """
        sink = self._usage_sink
        if sink is None or payload.event_id is None:
            return []
        shown = [(item.memory_id, item.content) for item in payload.memories]
        signals = attribute_overlap(payload.event_id, shown, output)
        for signal in signals:
            sink.emit(signal)
        return signals
