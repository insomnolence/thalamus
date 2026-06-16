"""Shared structural-anchoring primitives — the cue→code and memory→code joins.

Three small, stateless functions that turn a cue or a set of memories into the
structural nodes they are *about*, reused across the retrieval and (future) planning
paths so the cue→anchor→cross-link→resolve→k-hop chain is written once:

- :func:`anchor_nodes` — the code a *cue* is about (its top direct structural hits).
- :func:`resolve_and_expand` — a single node ref → that node plus its k-hop neighbourhood,
  tolerating a stale ref (the anchored node gone from the current graph, §13.18-D2).
- :func:`linked_nodes_for` — the code a set of *memories* is about (their cross-links,
  resolved + expanded, deduped).

Pure structural domain: no payload/gateway types, so the gateway's recall rung
(:class:`~thalamus.gateway.gateway.StructuralRelevanceRetriever`) and the planner both
depend on these rather than on each other.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from thalamus.core.types import Cue, MemoryRef, StructuralRef
from thalamus.structural.cross_link import CrossLinkIndex
from thalamus.structural.graph import StructuralGraph
from thalamus.structural.index import ScoredNode, StructuralRetriever
from thalamus.structural.schema import StructuralNode


def ranked_hits(
    cue: Cue,
    structural_retrievers: Sequence[StructuralRetriever],
    *,
    k: int,
    min_relevance: float,
) -> list[tuple[str, ScoredNode]]:
    """The cue's direct structural hits above ``min_relevance``, as ``(corpus, hit)`` ranked
    across corpora by relevance — each corpus searches its own index (no pollution).

    The shared primitive behind both the gateway's direct-hit payload section and the
    planner's target resolution: "which code/doc nodes does this text match, best first".
    """
    hits: list[tuple[str, ScoredNode]] = []
    for retriever in structural_retrievers:
        for scored in retriever.retrieve(cue, k):
            if scored.score > min_relevance:
                hits.append((retriever.corpus, scored))
    hits.sort(key=lambda item: item[1].score, reverse=True)
    return hits


def anchor_nodes(
    cue: Cue,
    structural_retrievers: Sequence[StructuralRetriever],
    *,
    max_anchors: int,
    min_relevance: float,
) -> frozenset[StructuralRef]:
    """The structural nodes the ``cue`` is *about* — its top direct structural hits, above floor.

    Runs each corpus retriever, keeps the best score per node above ``min_relevance``, and
    returns the top ``max_anchors`` refs across corpora — the query-local anchor set the
    cross-hemisphere join hangs off (§13.19).
    """
    scored_by_ref: dict[StructuralRef, float] = {}
    for retriever in structural_retrievers:
        for scored in retriever.retrieve(cue, max_anchors):
            if scored.score > min_relevance:
                ref = scored.node.ref
                scored_by_ref[ref] = max(scored_by_ref.get(ref, 0.0), scored.score)
    top = sorted(scored_by_ref.items(), key=lambda kv: kv[1], reverse=True)[:max_anchors]
    return frozenset(ref for ref, _ in top)


def resolve_and_expand(
    graph: StructuralGraph, ref: StructuralRef, *, k_hop: int
) -> list[StructuralNode]:
    """A single node ``ref`` → that node plus its ``k_hop`` neighbourhood.

    Returns ``[]`` when the ref is stale — the anchored node is gone from the current graph
    (the §13.18-D2 staleness signal). Flagging/surfacing staleness is the caller's job; here
    a stale ref simply contributes nothing.
    """
    node = graph.get(ref)
    if node is None:
        return []
    return [node, *graph.k_hop(ref, k_hop)]


def linked_nodes_for(
    memories: Iterable[MemoryRef],
    graph: StructuralGraph,
    links: CrossLinkIndex,
    *,
    k_hop: int,
) -> list[StructuralNode]:
    """The structural nodes a set of ``memories`` is about — their cross-links, resolved.

    For each memory, its cross-linked node refs (``links.nodes_for``) are resolved and
    k-hop-expanded, deduplicated by ``node_id`` in first-seen order across the input.
    """
    seen: set[str] = set()
    out: list[StructuralNode] = []
    for memory in memories:
        for node_ref in links.nodes_for(memory):
            for node in resolve_and_expand(graph, node_ref, k_hop=k_hop):
                if node.node_id not in seen:
                    seen.add(node.node_id)
                    out.append(node)
    return out
