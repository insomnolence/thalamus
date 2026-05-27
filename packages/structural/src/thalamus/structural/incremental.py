"""Content-hashed incremental Brain-2 ingestion (scale, §14.1).

Re-embedding every node on each build is the dominant Brain-2 start cost. This re-parses the
repo (cheap relative to embedding) but re-embeds only the nodes of files whose content hash
changed since the last build (tracked in a :class:`FileManifest`), drops vanished files'
nodes, and reuses the persisted embeddings of unchanged files — O(changes), not O(repo).

Cross-file edges stay correct because *all* current edges are re-MERGEd each build (cheap),
so an edge dropped when one endpoint's file was re-ingested is immediately re-created; only
the embedding work is skipped.

The §14.1 backstop: this is a CACHE over a re-derivable graph. A from-scratch full rebuild
(fresh graph/index/manifest) must reproduce the incremental result exactly — the property the
test asserts, and what a forced ``--rebuild`` does (drop the manifest, rebuild from source).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from thalamus.core.protocols import Encoder
from thalamus.core.types import Scope, StructuralRef
from thalamus.structural.graph import StructuralGraph
from thalamus.structural.index import StructuralIndex, node_text
from thalamus.structural.ingestor import Ingestor
from thalamus.structural.manifest import FileManifest, ManifestEntry
from thalamus.structural.schema import IngestResult, StructuralEdge, StructuralNode


@dataclass(frozen=True, slots=True)
class CorpusSpec:
    """One Brain-2 corpus: its ingestor + the (separate, no-pollution) index it embeds into."""

    ingestor: Ingestor
    index: StructuralIndex
    corpus: str = "code"


@dataclass(frozen=True, slots=True)
class IngestStats:
    """What an incremental build did — surfaces the embedding-skip invariant (0 on no-change)."""

    files: int
    changed: int
    vanished: int
    embedded: int  # nodes re-embedded
    removed: int  # nodes dropped


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def incremental_ingest(
    repo: Path,
    scope: Scope,
    *,
    corpora: Sequence[CorpusSpec],
    graph: StructuralGraph,
    manifest: FileManifest,
    encoder: Encoder,
) -> IngestStats:
    """Re-derive Brain 2 into ``graph`` + the corpora's indexes, re-embedding only changed files.

    With a persistent graph/index/manifest (Neo4j, or held across calls) a no-change rebuild
    does zero embedding work; with fresh ones it is a full build (the re-derive oracle)."""
    # 1. Parse every corpus (whole-repo). Route each node to its corpus index; group anchored
    #    nodes by source file (the unit of change detection).
    all_nodes: list[StructuralNode] = []
    all_edges: list[StructuralEdge] = []
    index_of: dict[str, StructuralIndex] = {}
    path_node_ids: dict[str, list[str]] = {}
    for spec in corpora:
        result = spec.ingestor.ingest_path(repo, scope)
        all_edges.extend(result.edges)
        for node in result.nodes:
            all_nodes.append(node)
            index_of[node.node_id] = spec.index
            if node.anchor is not None:
                path_node_ids.setdefault(node.anchor.path, []).append(node.node_id)

    # 2. Hash current files; diff against the last build.
    current_sha = {path: _sha256(Path(path)) for path in path_node_ids}
    previous = manifest.load(scope)
    changed = {
        path for path, sha in current_sha.items()
        if previous.get(path) is None or previous[path].sha256 != sha
    }
    vanished = {path for path in previous if path not in current_sha}

    # 3. Drop the nodes of changed + vanished files (old nodes/edges, and Neo4j embeddings).
    removed_ids = [
        node_id
        for path in (changed | vanished)
        if path in previous
        for node_id in previous[path].node_ids
    ]
    removed_refs = [StructuralRef(scope, node_id) for node_id in removed_ids]
    graph.remove(removed_refs)
    for spec in corpora:
        spec.index.remove(removed_refs)

    # 4. Re-MERGE all current nodes + edges (idempotent; preserves unchanged embeddings, and
    #    re-creates any cross-file edge dropped in step 3).
    graph.add(IngestResult(nodes=all_nodes, edges=all_edges))

    # 5. Re-embed ONLY changed/new files' nodes (the dominant cost, skipped for unchanged).
    to_embed = [
        node for node in all_nodes if node.anchor is not None and node.anchor.path in changed
    ]
    if to_embed:
        embeddings = encoder.encode([node_text(node) for node in to_embed])
        for node, embedding in zip(to_embed, embeddings, strict=True):
            index_of[node.node_id].add(node, embedding)

    # 6. Persist the current manifest (every file -> sha + node ids) for the next diff.
    manifest.save(
        scope,
        {
            path: ManifestEntry(current_sha[path], tuple(path_node_ids[path]))
            for path in current_sha
        },
    )

    return IngestStats(
        files=len(current_sha),
        changed=len(changed),
        vanished=len(vanished),
        embedded=len(to_embed),
        removed=len(removed_ids),
    )
