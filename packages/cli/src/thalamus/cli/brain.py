"""Two-hemisphere composition — assemble a recall-ready :class:`Gateway` over both brains.

The serve-time counterpart to the batch sync (``dogfood.py``): Brain 1 (experiential
episodes) is durable in the store; Brain 2 (the structural graph) is **re-derived** by
re-parsing the repo (it is re-derivable by design, §4); and the cross-hemisphere links
are **re-resolved** from the episodes' footprints against the current AST (§13.19 — a
derived view, §14.1). So a fresh process rebuilds Brain 2 + links from the durable
episodes + the current code, then serves structure-aware recall ("editing this surfaces
why we did it / what bit us here").

Kept in the composition root (not a library) because it wires concretes across packages.
``episodes`` are passed in — from the just-run sync, or (for a cold serve) a future scan
of the experiential store. Neo4j-backed structural graph + native cross-edges are the
next increment; for now Brain 2 + links are in-memory, re-derived each start.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from thalamus.core.protocols import Encoder, Store
from thalamus.core.types import MemoryRecord
from thalamus.gateway import Gateway
from thalamus.retrieval import L0Retriever
from thalamus.structural import (
    CrossLinkIndex,
    Ingestor,
    InMemoryCrossLinkIndex,
    InMemoryStructuralGraph,
    PythonAstIngestor,
    StructuralGraph,
    link_by_footprint,
)


def build_two_hemisphere_gateway(
    repo: Path,
    *,
    store: Store,
    encoder: Encoder,
    episodes: Sequence[MemoryRecord],
    ingestor: Ingestor | None = None,
    graph: StructuralGraph | None = None,
    links: CrossLinkIndex | None = None,
    k: int = 5,
    k_hop: int = 1,
) -> Gateway:
    """Re-derive Brain 2 + cross-links from ``repo`` and return a two-hemisphere gateway.

    ``store`` holds Brain 1 (episodes); ``episodes`` are the records whose footprints to
    link. ``graph``/``links`` default to in-memory (re-derived per call); pass
    Neo4j-backed implementations to persist Brain 2 + native cross-edges in the shared
    substrate. Returns a :class:`Gateway` whose recall fuses experiential memories with
    the structural nodes they touched (plus their ``k_hop`` neighbours)."""
    ingestor = ingestor or PythonAstIngestor()
    result = ingestor.ingest_path(repo)
    graph = graph if graph is not None else InMemoryStructuralGraph()
    graph.add(result)

    links = links if links is not None else InMemoryCrossLinkIndex()
    footprints = [
        (episode.memory_id, tuple(episode.metadata.get("footprint", ()))) for episode in episodes
    ]
    link_by_footprint(footprints, result.nodes, links, repo_root=repo)

    retriever = L0Retriever(encoder, store)
    return Gateway(retriever, k=k, graph=graph, links=links, structural_k_hop=k_hop)
