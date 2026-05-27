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
from collections.abc import Callable, Sequence
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
    """One Brain-2 corpus: its ingestor, the (separate, no-pollution) index it embeds into,
    and a ``files`` enumerator (e.g. ``python_files``) used to detect changes *without* parsing —
    so a no-change rebuild never runs the ingestor (and its ~9s jedi pass)."""

    ingestor: Ingestor
    index: StructuralIndex
    files: Callable[[Path], list[Path]]
    corpus: str = "code"


@dataclass(frozen=True, slots=True)
class IngestStats:
    """What an incremental build did — surfaces the embedding-skip invariant (0 on no-change)."""

    files: int
    changed: int
    vanished: int
    embedded: int  # nodes re-embedded
    removed: int  # nodes dropped


@dataclass(frozen=True, slots=True)
class IncrementalResult:
    """The outcome of an incremental build: what it did + the per-corpus parse results.

    ``results`` (corpus -> the full :class:`IngestResult`) lets the caller reuse the parse for
    footprint linking + staleness without re-parsing — the parse is whole-repo either way; only
    embedding is incremental."""

    stats: IngestStats
    results: dict[str, IngestResult]  # per corpus; empty when nothing changed (rebuilt=False)
    rebuilt: bool  # False when a no-change build skipped parse/jedi/embed entirely


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
) -> IncrementalResult:
    """Re-derive Brain 2 into ``graph`` + the corpora's indexes, re-embedding only changed files.

    With a persistent graph/index/manifest (Neo4j, or held across calls) a no-change rebuild
    returns early without parsing — O(hash files), independent of repo size; with fresh ones it
    is a full build (the re-derive oracle)."""
    # 1. Cheap: enumerate + hash corpus files (NO parse, NO jedi) and diff against the last build.
    current_sha: dict[str, str] = {}
    for spec in corpora:
        for path in spec.files(repo):
            current_sha[str(path)] = _sha256(path)
    previous = manifest.load(scope)
    changed = {
        path for path, sha in current_sha.items()
        if previous.get(path) is None or previous[path].sha256 != sha
    }
    vanished = {path for path in previous if path not in current_sha}

    # 2. No change -> the persisted graph + indexes are already current. Skip parse/jedi/embed/write
    #    entirely (the scale win: a warm restart does no O(repo) work).
    if not changed and not vanished:
        return IncrementalResult(
            stats=IngestStats(len(current_sha), 0, 0, 0, 0), results={}, rebuilt=False
        )

    # 3. Something changed -> parse every corpus (the ~9s jedi pass runs HERE, only when needed).
    all_nodes: list[StructuralNode] = []
    all_edges: list[StructuralEdge] = []
    path_node_ids: dict[str, list[str]] = {}
    results: dict[str, IngestResult] = {}
    for spec in corpora:
        result = spec.ingestor.ingest_path(repo, scope)
        results[spec.corpus] = result
        all_edges.extend(result.edges)
        for node in result.nodes:
            all_nodes.append(node)
            if node.anchor is not None:
                path_node_ids.setdefault(node.anchor.path, []).append(node.node_id)

    # 4. Drop the nodes of changed + vanished files (old nodes/edges, and Neo4j embeddings).
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

    # 5. Re-MERGE all current nodes + edges (idempotent; preserves unchanged embeddings, and
    #    re-creates any cross-file edge dropped in step 4).
    graph.add(IngestResult(nodes=all_nodes, edges=all_edges))

    # 6. Re-embed ONLY changed/new files' nodes (the dominant cost, skipped for unchanged),
    #    batched per corpus index (one write, not one round-trip per node).
    embedded = 0
    for spec in corpora:
        nodes = [
            node for node in results[spec.corpus].nodes
            if node.anchor is not None and node.anchor.path in changed
        ]
        if nodes:
            embeddings = encoder.encode([node_text(node) for node in nodes])
            spec.index.add_many(list(zip(nodes, embeddings, strict=True)))
            embedded += len(nodes)

    # 7. Persist the manifest keyed by the authoritative file set (every file -> sha + node ids).
    manifest.save(
        scope,
        {
            path: ManifestEntry(current_sha[path], tuple(path_node_ids.get(path, ())))
            for path in current_sha
        },
    )

    stats = IngestStats(
        files=len(current_sha),
        changed=len(changed),
        vanished=len(vanished),
        embedded=embedded,
        removed=len(removed_ids),
    )
    return IncrementalResult(stats=stats, results=results, rebuilt=True)
