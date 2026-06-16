"""The Gateway — the single conduit the actuator talks to.

Cue in, assembled ``ContextPayload`` out. Depends on the ``core.Retriever``
protocol for experiential recall, and *optionally* on a structural graph +
cross-hemisphere link index (Brain 2) to enrich the payload with related code
(§13.19). The MCP transport is a thin adapter on top (``server.py``); this class
is pure and testable. Without the structural collaborators it works experiential-only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from thalamus.core.protocols import Retriever, Store
from thalamus.core.types import (
    Cue,
    EventId,
    MemoryId,
    MemoryRef,
    RetrievalResult,
    Scope,
    ScoredMemory,
    SessionId,
    StructuralRef,
    Supersession,
)
from thalamus.gateway.payload import CallRelation, ContextPayload, MemoryItem, StructuralItem
from thalamus.gateway.views import DerivedViews, DerivedViewsRef
from thalamus.instrumentation import UsageSignal, UsageSink, attribute_overlap
from thalamus.structural import (
    CrossLinkIndex,
    ScoredNode,
    StructuralGraph,
    StructuralRetriever,
    anchor_nodes,
    linked_nodes_for,
    ranked_hits,
)

# Bounds for the call-graph payload section — kept selective (the §5.6 packaging discipline):
# only the cue's top few code hits, each with a capped caller/callee list.
_MAX_CALL_RELATIONS = 3
_MAX_CALL_NEIGHBORS = 6


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


class StructuralRelevanceRetriever:
    """Lift memories cross-linked to the code the cue is *about* — query-local structural relevance.

    Where :class:`StructuralLinkedRetriever` fires on an explicit ``focus``, this derives the anchor
    code nodes from the cue's *own* structural retrieval (the nodes it semantically matches), then
    RRF-boosts experiential candidates cross-linked to those anchors — "surface the *why* behind the
    code your query touched" (§13.19, the plan-tool fusion, made query-local). The first slice of
    L-R2 / the relevance-credibility "well-connected to Brain-2" signal.

    Bounded to the inner's candidate pool (it *promotes* a connected memory, never summons an
    irrelevant one) and gentle (RRF rank fusion, like the usage/hybrid rungs — it doesn't dominate
    relevance). Firewall-clean: the signal is the deterministic cross-link graph, not the model
    grading prose. A no-op when there are no structural retrievers / links / anchors, so an
    experiential-only brain or a query unrelated to code is unchanged.

    v1 boosts existing candidates only (not yet injecting connected memories the relevance pool
    missed — that is the explicit-focus path's job, and the plan tool's). It runs its own structural
    retrieval (the gateway runs another for the payload's direct hits — a deliberate v1 cost so the
    boost stays *in the chain* and is therefore captured in the off-policy log)."""

    def __init__(
        self,
        inner: Retriever,
        links: CrossLinkIndex,
        structural_retrievers: Sequence[StructuralRetriever],
        *,
        max_anchors: int = 5,
        min_relevance: float = 0.0,
        rrf_k: int = 60,
        weight: float = 1.0,
    ) -> None:
        self._inner = inner
        self._links = links
        self._structural_retrievers = tuple(structural_retrievers)
        self._max_anchors = max_anchors
        self._min_relevance = min_relevance
        self._rrf_k = rrf_k
        self._weight = weight

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        base = self._inner.retrieve(cue, k)
        anchors = anchor_nodes(
            cue,
            self._structural_retrievers,
            max_anchors=self._max_anchors,
            min_relevance=self._min_relevance,
        )
        if not anchors:
            return base

        # Structural-relevance rank: candidates cross-linked to an anchor node, most-linked first.
        overlaps = [
            (sum(1 for ref in self._links.nodes_for(c.record.ref) if ref in anchors), c)
            for c in base.candidates
        ]
        linked = sorted(
            ((n, c) for n, c in overlaps if n > 0), key=lambda t: t[0], reverse=True
        )
        if not linked:
            return base
        struct_rank = {c.record.ref: rank for rank, (_, c) in enumerate(linked, start=1)}

        fused: list[ScoredMemory] = []
        for rel_rank, candidate in enumerate(base.candidates, start=1):
            score = 1.0 / (self._rrf_k + rel_rank)  # relevance leg
            features = {**candidate.features, "relevance_rank": float(rel_rank)}
            srank = struct_rank.get(candidate.record.ref)
            if srank is not None:  # the cue's code overlaps this memory's cross-links
                score += self._weight * (1.0 / (self._rrf_k + srank))
                features["structural_relevance_rank"] = float(srank)
            features["structural_fused"] = score
            fused.append(
                ScoredMemory(record=candidate.record, score=candidate.score, features=features)
            )
        fused.sort(key=lambda scored: scored.features["structural_fused"], reverse=True)
        return RetrievalResult(cue=cue, candidates=fused, shown=fused[: max(k, 0)])


class SupersededDemotingRetriever:
    """Demote superseded beliefs below all current ones before the top-k cut (§13.18 R1).

    Realizes "current truth = the un-superseded frontier" as a *view*: a superseded belief
    still exists and can still surface (its history stays recallable), but it never out-ranks
    a current belief for a shown slot. Stable within each partition (preserves the inner
    retriever's relevance order). A no-op when nothing is superseded. Sits below the logging
    decorator so the retrieval-event log records the demoted (true) shown order.
    """

    def __init__(
        self,
        inner: Retriever,
        superseded: Mapping[MemoryRef, Supersession] | None = None,
        *,
        views: DerivedViewsRef | None = None,
    ) -> None:
        self._inner = inner
        # Share the gateway's DerivedViews holder when given (so one refresh reaches both the
        # demotion here and the annotation in the Gateway); otherwise hold a private snapshot
        # built from the legacy ``superseded`` map (back-compat for callers that never refresh).
        self._views = views if views is not None else DerivedViewsRef(
            DerivedViews(superseded=dict(superseded) if superseded else {})
        )

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        base = self._inner.retrieve(cue, k)
        superseded = self._views.views.superseded  # snapshot once: consistent across this call
        if not superseded:
            return base
        current = [c for c in base.candidates if c.record.ref not in superseded]
        replaced = [c for c in base.candidates if c.record.ref in superseded]
        if not replaced:
            return base
        candidates = [*current, *replaced]
        return RetrievalResult(
            cue=cue,
            candidates=candidates,
            shown=candidates[: max(k, 0)],
            event_id=base.event_id,
        )


class Gateway:
    """Assembles context for a cue by querying the brain through a retriever."""

    def __init__(
        self,
        retriever: Retriever,
        *,
        k: int = 5,
        graph: StructuralGraph | None = None,
        links: CrossLinkIndex | None = None,
        structural_retrievers: Sequence[StructuralRetriever] | None = None,
        structural_k_hop: int = 0,
        structural_min_relevance: float = 0.0,
        stale_references: Mapping[MemoryRef, Sequence[str]] | None = None,
        superseded: Mapping[MemoryRef, Supersession] | None = None,
        views: DerivedViewsRef | None = None,
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
        # One retriever per Brain-2 corpus (code / docs / …), each over its own index — direct
        # hits are ranked across corpora by relevance but grouped into per-corpus payload sections.
        self._structural_retrievers = tuple(structural_retrievers) if structural_retrievers else ()
        self._structural_k_hop = structural_k_hop
        # Refreshable derived views — the ``superseded`` (§13.18 R1) and ``stale_references``
        # (§13.18-D2) maps, bundled behind one holder so a dreaming refresh swaps them atomically
        # (see views.py). Share the caller's holder when given (so the upstream demoting retriever
        # sees the same swap); otherwise build a private snapshot from the legacy kwargs, which is
        # the back-compat path for callers that compose these once and never refresh.
        self._views = views if views is not None else DerivedViewsRef(
            DerivedViews(
                superseded=dict(superseded) if superseded else {},
                stale_references=dict(stale_references) if stale_references else {},
            )
        )
        # Floor for direct structural hits: don't append weakly-related code to every recall
        # (the bounded/selective constraint). Default is an encoder-agnostic noise floor
        # (strictly positive); a meaningful BGE threshold is deferred to real-usage tuning.
        self._structural_min_relevance = structural_min_relevance
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
        # Snapshot the derived views once for the whole call — a concurrent refresh swaps the
        # bundle atomically, so this recall sees a single consistent (old or new) snapshot.
        views = self._views.views
        result = self._retriever.retrieve(cue, self._k)
        memories = [
            MemoryItem.from_scored(
                scored,
                max_content_chars=self._max_memory_chars,
                stale_references=views.stale_references.get(scored.record.ref, ()),
                superseded=views.superseded.get(scored.record.ref),
            )
            for scored in result.shown
        ]
        direct = self._direct_hits(cue)
        structural = self._structural_for([scored.record.ref for scored in result.shown])
        # Cross-linked nodes (the §13.19 headline — "the code this memory is about") come
        # first; direct semantic hits fill the rest, deduped and tagged by corpus.
        exclude = {item.node_id for item in structural}
        for corpus, scored in direct:
            if scored.node.node_id not in exclude:
                exclude.add(scored.node.node_id)
                structural.append(StructuralItem.from_scored_node(scored, corpus=corpus))
        return ContextPayload(
            cue_text=prompt,
            memories=memories,
            structural=structural[: self._max_structural_items],
            structural_omitted=max(len(structural) - self._max_structural_items, 0),
            calls=self._call_relations([scored for _, scored in direct]),
            event_id=result.event_id,
        )

    @property
    def graph(self) -> StructuralGraph | None:
        """The structural graph this gateway queries (Brain 2), or None if experiential-only.

        Exposed so a dreaming structural-refresh pass can update the *same* handle the gateway
        reads — for a Neo4j-backed graph any client sees the write; for in-memory it must be the
        same instance, which this guarantees."""
        return self._graph

    @property
    def links(self) -> CrossLinkIndex | None:
        """The cross-hemisphere link index this gateway queries, or None if unused."""
        return self._links

    def refresh(self, views: DerivedViews) -> None:
        """Swap in freshly-recomputed derived views (the dreaming refresh seam).

        Atomic and lock-free: a single attribute assignment in the shared holder. Reaches both
        this gateway's annotation and the upstream demoting retriever's promotion when they share
        the holder (the composition root wires that). In-flight recalls keep their snapshot;
        subsequent recalls see the new views.
        """
        self._views.refresh(views)

    def _direct_hits(self, cue: Cue) -> list[tuple[str, ScoredNode]]:
        """Direct structural hits above the relevance floor, as ``(corpus, hit)`` ranked across
        corpora — each corpus searches its own index (no pollution), then merged by relevance."""
        return ranked_hits(
            cue,
            self._structural_retrievers,
            k=self._max_structural_items,
            min_relevance=self._structural_min_relevance,
        )

    def _call_relations(self, direct: Sequence[ScoredNode]) -> list[CallRelation]:
        """Callers/callees of the cue's top direct code hits — the call graph, surfaced.

        Reverse `calls` edges (callers) answer "what breaks if I change this"; forward
        edges (callees) are what it uses. Bounded to a few hits with capped neighbours."""
        graph = self._graph
        if graph is None:
            return []
        relations: list[CallRelation] = []
        for scored in direct:
            node = scored.node
            if node.kind not in ("function", "method", "class"):
                continue
            callers = graph.neighbors(node.ref, edge_types=("calls",), direction="in")
            callees = graph.neighbors(node.ref, edge_types=("calls",), direction="out")
            if not callers and not callees:
                continue
            relations.append(
                CallRelation(
                    label=node.label,
                    callers=tuple(n.label for n in callers[:_MAX_CALL_NEIGHBORS]),
                    callees=tuple(n.label for n in callees[:_MAX_CALL_NEIGHBORS]),
                )
            )
            if len(relations) >= _MAX_CALL_RELATIONS:
                break
        return relations

    def _structural_for(self, memories: Sequence[MemoryRef]) -> list[StructuralItem]:
        graph, links = self._graph, self._links
        if graph is None or links is None:
            return []
        return [
            StructuralItem.from_node(node)
            for node in linked_nodes_for(memories, graph, links, k_hop=self._structural_k_hop)
        ]

    def record_outcome(self, payload: ContextPayload, output: str) -> list[UsageSignal]:
        """Attribute Tier-1 usage: which surfaced memories the actuator's ``output`` used.

        Stateless — pass back the payload from :meth:`recall`. Signals are logged to the
        usage sink keyed by the payload's ``event_id`` (the loop close: surfaced → used).
        """
        if payload.event_id is None:
            return []
        shown = [(item.memory_id, item.content) for item in payload.memories]
        return self.record_outcome_for(payload.event_id, shown, output)

    def record_outcome_for(
        self, event_id: EventId, shown: Sequence[tuple[MemoryId, str]], output: str
    ) -> list[UsageSignal]:
        """Record Tier-1 usage from an ``event_id`` + the shown ``(memory_id, content)`` pairs.

        The reconstruction-friendly form of :meth:`record_outcome`: a caller that no longer holds
        the live payload (a ``record_usage`` arriving after a serve restart, or in another worker
        of the long-running server) rebuilds ``shown`` from the durable retrieval-event log + store
        and calls this — so the citation signal survives instead of being silently dropped."""
        sink = self._usage_sink
        if sink is None:
            return []
        signals = attribute_overlap(event_id, shown, output)
        for signal in signals:
            sink.emit(signal)
        return signals
