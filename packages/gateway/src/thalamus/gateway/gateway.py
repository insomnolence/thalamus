"""The Gateway — the single conduit the actuator talks to.

Cue in, assembled ``ContextPayload`` out. Depends on the ``core.Retriever``
protocol for experiential recall, and *optionally* on a structural graph +
cross-hemisphere link index (Brain 2) to enrich the payload with related code
(§13.19). The MCP transport is a thin adapter on top (``server.py``); this class
is pure and testable. Without the structural collaborators it works experiential-only.
"""

from __future__ import annotations

from collections.abc import Sequence

from thalamus.core.protocols import Retriever, Store
from thalamus.core.types import (
    Cue,
    MemoryRef,
    RetrievalResult,
    Scope,
    ScoredMemory,
    SessionId,
    StructuralRef,
)
from thalamus.gateway.payload import ContextPayload, MemoryItem, StructuralItem
from thalamus.instrumentation import UsageSignal, UsageSink, attribute_overlap
from thalamus.structural import CrossLinkIndex, StructuralGraph, StructuralNode


def _focus_node_ref(scope: Scope, focus: str) -> StructuralRef:
    module = focus.removesuffix(".py").replace("\\", "/").strip("/").replace("/", ".")
    if module.endswith(".__init__"):
        module = module[: -len(".__init__")]
    return StructuralRef(scope, f"module:{module}")


class StructuralLinkedRetriever:
    """Add memories connected to a focused structural node to a base retrieval result."""

    def __init__(
        self,
        inner: Retriever,
        store: Store,
        graph: StructuralGraph,
        links: CrossLinkIndex,
        *,
        k_hop: int = 1,
    ) -> None:
        self._inner = inner
        self._store = store
        self._graph = graph
        self._links = links
        self._k_hop = k_hop

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        base = self._inner.retrieve(cue, k)
        if cue.focus is None:
            return base
        anchor = _focus_node_ref(cue.scope, cue.focus)
        node = self._graph.get(anchor)
        if node is None:
            return base
        refs = [node.ref, *(near.ref for near in self._graph.k_hop(node.ref, self._k_hop))]
        extras: list[ScoredMemory] = []
        for distance, ref in enumerate(refs):
            for memory in self._links.memories_for(ref):
                record = self._store.get(memory)
                if record is not None:
                    extras.append(
                        ScoredMemory(
                            record=record,
                            score=2.0 if distance == 0 else 1.9,
                            features={
                                "structural_link": 1.0,
                                "structural_seed": float(distance == 0),
                                "structural_expanded": float(distance > 0),
                            },
                        )
                    )
        ranked: dict[MemoryRef, ScoredMemory] = {item.record.ref: item for item in base.candidates}
        for item in extras:
            prior = ranked.get(item.record.ref)
            if prior is None or item.score > prior.score:
                ranked[item.record.ref] = item
        candidates = sorted(ranked.values(), key=lambda item: item.score, reverse=True)
        return RetrievalResult(cue=cue, candidates=candidates, shown=candidates[: max(k, 0)])


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
        max_structural_items: int = 12,
        max_memory_chars: int = 1000,
        usage_sink: UsageSink | None = None,
    ) -> None:
        if max_structural_items < 0 or max_memory_chars < 1:
            raise ValueError("payload bounds must be non-negative with positive memory text limit")
        self._retriever = retriever
        self._k = k
        self._graph = graph
        self._links = links
        self._structural_k_hop = structural_k_hop
        self._max_structural_items = max_structural_items
        self._max_memory_chars = max_memory_chars
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
        memories = [
            MemoryItem.from_scored(scored, max_content_chars=self._max_memory_chars)
            for scored in result.shown
        ]
        structural = self._structural_for([scored.record.ref for scored in result.shown])
        return ContextPayload(
            cue_text=prompt,
            memories=memories,
            structural=structural[: self._max_structural_items],
            structural_omitted=max(len(structural) - self._max_structural_items, 0),
            event_id=result.event_id,
        )

    def _structural_for(self, memories: Sequence[MemoryRef]) -> list[StructuralItem]:
        graph, links = self._graph, self._links
        if graph is None or links is None:
            return []
        seen: set[str] = set()
        items: list[StructuralItem] = []
        for memory in memories:
            for node_ref in links.nodes_for(memory):
                for node in self._resolve(graph, node_ref):
                    if node.node_id not in seen:
                        seen.add(node.node_id)
                        items.append(StructuralItem.from_node(node))
        return items

    def _resolve(self, graph: StructuralGraph, ref: StructuralRef) -> list[StructuralNode]:
        node = graph.get(ref)
        if node is None:
            # Stale link: the anchored node is gone from the current graph — the
            # §13.18-D2 staleness signal. Skipped for now; flagged/surfaced later.
            return []
        return [node, *graph.k_hop(ref, self._structural_k_hop)]

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
