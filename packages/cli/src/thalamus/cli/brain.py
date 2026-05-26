"""Two-hemisphere composition — assemble a recall-ready :class:`Gateway` over both brains.

The serve-time counterpart to the batch sync (``dogfood.py``): Brain 1 (experiential
episodes) is durable in the store; Brain 2 (the structural graph) is **re-derived** by
re-parsing the repo (it is re-derivable by design, §4); and the cross-hemisphere links
are **re-resolved** from the episodes' footprints against the current AST (§13.19 — a
derived view, §14.1). So a fresh process rebuilds Brain 2 + links from the durable
episodes + the current code, then serves structure-aware recall ("editing this surfaces
why we did it / what bit us here").

Kept in the composition root (not a library) because it wires concretes across packages.
``episodes`` are passed in — from the just-run sync, or (for a cold serve) ``Store.scan``
of the experiential store. ``graph``/``links`` may be in-memory (re-derived each start) or
Neo4j-backed (persisted, with native cross-edges).
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from thalamus.core.protocols import Encoder, Retriever, Store
from thalamus.core.types import Hemisphere, MemoryRecord, Scope
from thalamus.gateway import Gateway, StructuralLinkedRetriever
from thalamus.instrumentation import EventSink, LoggingRetriever, UsageSink
from thalamus.retrieval import L0Retriever
from thalamus.store import InMemoryStore, Neo4jStore, connect
from thalamus.structural import (
    CompositeIngestor,
    CrossLinkIndex,
    Ingestor,
    InMemoryCrossLinkIndex,
    InMemoryStructuralGraph,
    InMemoryStructuralIndex,
    JediCallIngestor,
    PythonAstIngestor,
    StructuralGraph,
    StructuralRetriever,
    footprint_staleness,
    link_by_footprint,
    node_text,
)

logger = logging.getLogger(__name__)


def _default_ingestor(resolve_calls: bool) -> Ingestor:
    """The AST structure pass, composed with jedi call resolution when available.

    Degrades gracefully: if ``resolve_calls`` is on but jedi isn't installed, structure
    is still ingested (calls omitted) with a warning, rather than failing."""
    ast_pass = PythonAstIngestor()
    if not resolve_calls:
        return ast_pass
    if importlib.util.find_spec("jedi") is None:
        logger.warning("resolve_calls requested but jedi is not installed; calls edges disabled")
        return ast_pass
    return CompositeIngestor([ast_pass, JediCallIngestor()])


def build_two_hemisphere_gateway(
    repo: Path,
    *,
    store: Store,
    encoder: Encoder,
    scope: Scope,
    episodes: Sequence[MemoryRecord],
    ingestor: Ingestor | None = None,
    graph: StructuralGraph | None = None,
    links: CrossLinkIndex | None = None,
    k: int = 5,
    k_hop: int = 1,
    resolve_calls: bool = True,
    structural_min_relevance: float = 0.0,
    max_structural_items: int = 12,
    max_memory_chars: int = 1000,
    event_sink: EventSink | None = None,
    usage_sink: UsageSink | None = None,
) -> Gateway:
    """Re-derive Brain 2 + cross-links from ``repo`` and return a two-hemisphere gateway.

    ``store`` holds Brain 1 (episodes); ``episodes`` are the records whose footprints to
    link. ``graph``/``links`` default to in-memory (re-derived per call); pass
    Neo4j-backed implementations to persist Brain 2 + native cross-edges in the shared
    substrate. Returns a :class:`Gateway` whose recall fuses experiential memories with
    the structural nodes they touched (plus their ``k_hop`` neighbours)."""
    ingestor = ingestor if ingestor is not None else _default_ingestor(resolve_calls)
    result = ingestor.ingest_path(repo, scope)
    graph = graph if graph is not None else InMemoryStructuralGraph(scope)
    graph.replace(result)

    links = links if links is not None else InMemoryCrossLinkIndex()
    footprints = [
        (episode.ref, tuple(episode.metadata.get("footprint", ()))) for episode in episodes
    ]
    link_by_footprint(footprints, result.nodes, links, repo_root=repo)

    # §13.18-D2: flag curated memories whose footprint files are gone from disk (stale beliefs
    # about code that no longer exists). Episodes are immutable history, so only curated memories
    # are staleness-checked. Computed here where the repo root is known; the gateway just surfaces.
    stale_references = footprint_staleness(
        [
            (record.ref, tuple(record.metadata.get("footprint", ())))
            for record in episodes
            if record.metadata.get("source") == "curated"
        ],
        repo_root=repo,
    )

    # Direct structural retrieval: embed Brain 2 nodes into their own (separate) index so a
    # cue can hit code directly, not only via cross-links. A derived view over the re-derived
    # graph (§14.1) — rebuilt here each start, cheap for repo-scale node counts.
    structural_index = InMemoryStructuralIndex(dim=encoder.dim)
    nodes = list(result.nodes)
    if nodes:
        embeddings = encoder.encode([node_text(node) for node in nodes])
        for node, embedding in zip(nodes, embeddings, strict=True):
            structural_index.add(node, embedding)
    structural_retriever = StructuralRetriever(encoder, structural_index)

    base = L0Retriever(encoder, store)
    retriever: Retriever = StructuralLinkedRetriever(base, store, graph, links, k_hop=k_hop)
    if event_sink is not None:
        retriever = LoggingRetriever(retriever, event_sink, policy_id="L0+structural")
    return Gateway(
        retriever,
        k=k,
        graph=graph,
        links=links,
        structural_retriever=structural_retriever,
        structural_k_hop=k_hop,
        structural_min_relevance=structural_min_relevance,
        stale_references=stale_references,
        max_structural_items=max_structural_items,
        max_memory_chars=max_memory_chars,
        usage_sink=usage_sink,
    )


def build_store(
    *,
    dim: int,
    neo4j_uri: str | None,
    neo4j_user: str,
    neo4j_password: str | None,
    hemisphere: Hemisphere = Hemisphere.EXPERIENTIAL,
    encoder_id: str | None = None,
) -> Store:
    """Neo4j-backed store when ``neo4j_uri`` is set (durable), else in-memory (warned)."""
    if neo4j_uri is not None:
        driver = connect(neo4j_uri, neo4j_user, neo4j_password or "")
        return Neo4jStore(dim=dim, driver=driver, hemisphere=hemisphere, encoder_id=encoder_id)
    print(
        "warning: THALAMUS_NEO4J_URI not set — using in-memory store (not durable).",
        file=sys.stderr,
    )
    return InMemoryStore(dim=dim)


def close_store(store: Store) -> None:
    """Close the store if it holds a closable resource (e.g. a Neo4j driver)."""
    close = getattr(store, "close", None)
    if callable(close):
        close()
