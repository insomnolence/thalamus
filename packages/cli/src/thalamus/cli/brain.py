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
from collections.abc import Callable, Sequence
from pathlib import Path

from thalamus.cli.project import CorpusConfig
from thalamus.core.exceptions import ThalamusError
from thalamus.core.protocols import Encoder, Retriever, Store, SupersessionIndex
from thalamus.core.types import Hemisphere, MemoryRecord, Scope
from thalamus.experiential import InMemorySupersessionIndex
from thalamus.gateway import (
    DerivedViews,
    DerivedViewsRef,
    Gateway,
    StructuralLinkedRetriever,
    SupersededDemotingRetriever,
)
from thalamus.instrumentation import EventSink, LoggingRetriever, UsageSink
from thalamus.retrieval import HybridRetriever, L0Retriever, LexicalRetriever
from thalamus.store import InMemoryStore, Neo4jStore, connect
from thalamus.structural import (
    CompositeIngestor,
    CorpusSpec,
    CrossLinkIndex,
    DocIngestor,
    FileManifest,
    Ingestor,
    IngestResult,
    InMemoryCrossLinkIndex,
    InMemoryFileManifest,
    InMemoryStructuralGraph,
    InMemoryStructuralIndex,
    JediCallIngestor,
    PythonAstIngestor,
    ScipIngestor,
    StructuralGraph,
    StructuralIndex,
    StructuralNode,
    StructuralRetriever,
    code_files,
    footprint_staleness,
    incremental_ingest,
    link_by_footprint,
    markdown_files,
    typescript_files,
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


def _scip_ingestor(scip_index: Path, *, root_package: str | None = None) -> Ingestor:
    """The language-agnostic SCIP ingestor (structure + precise calls in one pass).

    ``scip_index`` is a `.scip` artifact built out-of-band (e.g. ``scip-typescript``;
    see ``scripts/scip-index-typescript.sh``)."""
    if importlib.util.find_spec("google.protobuf") is None:
        raise ThalamusError(
            "SCIP ingestion needs the 'scip' extra: install thalamus-structural[scip]"
        )
    return ScipIngestor(scip_index, root_package=root_package)


def _code_ingestor(code_language: str, scip_index: Path | None, resolve_calls: bool) -> Ingestor:
    """Pick the code-corpus ingestor for the language (SCIP for non-Python)."""
    if code_language == "python":
        return _default_ingestor(resolve_calls)
    if code_language == "typescript":
        if scip_index is None:
            raise ThalamusError("code_language='typescript' requires a scip_index path")
        return _scip_ingestor(scip_index)
    raise ThalamusError(f"unknown code_language: {code_language!r} (python|typescript)")


def _code_files_for(code_language: str) -> Callable[[Path], list[Path]]:
    """The change-detection file enumerator for the code corpus' language."""
    return typescript_files if code_language == "typescript" else code_files


def _doc_corpus_label(root: Path) -> str:
    """A short, stable label for a doc root — its dir name, or the parent's when the dir is
    a generic ``docs``/``doc`` (so a sibling ``mcp-server/docs`` and ``dollhouse/docs`` differ)."""
    generic = {"docs", "doc", "documentation"}
    return root.parent.name if root.name.lower() in generic else root.name


def build_corpora(
    *,
    encoder: Encoder,
    ingestor: Ingestor | None = None,
    code_index: StructuralIndex | None = None,
    doc_index: StructuralIndex | None = None,
    doc_index_factory: Callable[[str], StructuralIndex] | None = None,
    doc_roots: Sequence[Path] | None = None,
    code_language: str = "python",
    scip_index: Path | None = None,
    resolve_calls: bool = True,
    resolve_docs: bool = True,
) -> list[CorpusSpec]:
    """The Brain-2 corpora: code (Python AST + jedi, or SCIP for TS/others) and docs (Markdown).

    Each corpus pairs an ingestor with its OWN (no-pollution) vector index and a change-detection
    file enumerator. Factored out of :func:`build_two_hemisphere_gateway` so the startup build and
    the live re-derive pass build the *same* corpora over the *same* index handles — the re-derive
    then re-ingests into exactly what recall queries. An explicitly injected ``ingestor`` overrides
    the language default; ``code_index``/``doc_index`` default to in-memory when not supplied."""
    code_ingestor = (
        ingestor if ingestor is not None
        else _code_ingestor(code_language, scip_index, resolve_calls)
    )
    code_index = code_index if code_index is not None else InMemoryStructuralIndex(dim=encoder.dim)
    corpora = [CorpusSpec(code_ingestor, code_index, _code_files_for(code_language), "code")]
    if doc_roots:
        # Each doc root is its own labeled corpus (own index + namespaced node ids → no
        # cross-root collision), surfaced as a "Related docs (<label>)" payload section.
        for root in doc_roots:
            label = _doc_corpus_label(root)
            corpus = f"docs ({label})"
            index = (
                doc_index_factory(corpus)
                if doc_index_factory is not None
                else InMemoryStructuralIndex(dim=encoder.dim)
            )
            corpora.append(
                CorpusSpec(
                    DocIngestor(id_namespace=label), index, markdown_files, corpus, root=root
                )
            )
    elif resolve_docs:
        doc_index = doc_index if doc_index is not None else InMemoryStructuralIndex(dim=encoder.dim)
        corpora.append(CorpusSpec(DocIngestor(), doc_index, markdown_files, "docs"))
    return corpora


def build_corpora_from_configs(
    configs: Sequence[CorpusConfig],
    *,
    encoder: Encoder,
    index_factory: Callable[[str], StructuralIndex] | None = None,
    resolve_calls: bool = True,
) -> list[CorpusSpec]:
    """Build Brain-2 corpora from declarative ``[[corpus]]`` configs — the language-agnostic path.

    Each corpus' ``kind`` resolves to a registered :class:`~thalamus.cli.producer_registry.Producer`
    (``python-ast``/``scip``/``docs``/``text`` built in; more via ``register_producer``) that yields
    the ingestor + change-detection enumerator; this just pairs that with the corpus' own
    (no-pollution) index (via ``index_factory``, else in-memory) and root. Adding a corpus kind is a
    producer registration, not an edit here — the index wiring stays the caller's concern."""
    # Lazy import for its registration side effect — and to keep the import graph cycle-free
    # (producers imports back into this module's ingestor factories).
    import thalamus.cli.producers  # noqa: F401 — registers the built-in producers
    from thalamus.cli.producer_registry import ProducerContext, get_producer

    def make_index(name: str) -> StructuralIndex:
        if index_factory is not None:
            return index_factory(name)
        return InMemoryStructuralIndex(dim=encoder.dim)

    ctx = ProducerContext(resolve_calls=resolve_calls)
    specs: list[CorpusSpec] = []
    for cfg in configs:
        built = get_producer(cfg.kind).build(cfg, ctx=ctx)
        specs.append(
            CorpusSpec(built.ingestor, make_index(cfg.name), built.files, cfg.name, root=cfg.root)
        )
    return specs


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
    code_index: StructuralIndex | None = None,
    doc_index: StructuralIndex | None = None,
    doc_roots: Sequence[Path] | None = None,
    doc_index_factory: Callable[[str], StructuralIndex] | None = None,
    corpora: Sequence[CorpusSpec] | None = None,
    manifest: FileManifest | None = None,
    supersession: SupersessionIndex | None = None,
    rebuild: bool = False,
    k: int = 5,
    k_hop: int = 1,
    code_language: str = "python",
    scip_index: Path | None = None,
    resolve_calls: bool = True,
    resolve_docs: bool = True,
    structural_min_relevance: float = 0.0,
    hybrid_retrieval: bool = True,
    max_structural_items: int = 12,
    max_memory_chars: int = 1000,
    event_sink: EventSink | None = None,
    usage_sink: UsageSink | None = None,
) -> Gateway:
    """Re-derive Brain 2 + cross-links from ``repo`` and return a two-hemisphere gateway.

    ``store`` holds Brain 1 (episodes); ``episodes`` are the records whose footprints to link.
    ``graph``/``code_index``/``doc_index``/``manifest`` default to in-memory (a full build each
    call); pass Neo4j-backed implementations to persist Brain 2 and rebuild **incrementally** —
    only files whose content hash changed are re-embedded (the dominant start cost at scale).
    ``rebuild=True`` forces the full re-derive oracle (drop the persisted graph + manifest).
    Returns a :class:`Gateway` whose recall fuses experiential memories with the structural
    nodes they touched (plus their ``k_hop`` neighbours)."""
    # Two re-derivable Brain-2 corpora: code (Python AST + jedi, or SCIP for TS/others) and docs
    # (Markdown), each over its OWN vector index (no-pollution) on the shared graph substrate. The
    # caller may inject a prebuilt ``corpora`` (so the live re-derive pass shares the exact same
    # specs); otherwise build them here from the language params.
    graph = graph if graph is not None else InMemoryStructuralGraph(scope)
    manifest = manifest if manifest is not None else InMemoryFileManifest()
    corpora = list(corpora) if corpora is not None else build_corpora(
        encoder=encoder,
        ingestor=ingestor,
        code_index=code_index,
        doc_index=doc_index,
        doc_index_factory=doc_index_factory,
        doc_roots=doc_roots,
        code_language=code_language,
        scip_index=scip_index,
        resolve_calls=resolve_calls,
        resolve_docs=resolve_docs,
    )

    if rebuild:  # force the full re-derive: clear persisted graph + manifest so all files are "new"
        graph.replace(IngestResult(nodes=[], edges=[]))
        manifest.save(scope, {})

    ingest = incremental_ingest(
        repo, scope, corpora=corpora, graph=graph, manifest=manifest, encoder=encoder
    )
    # Module nodes to link episode footprints against: the fresh parse if Brain 2 was rebuilt (from
    # ALL corpora — corpus names are arbitrary under [[corpus]], and link_by_footprint's
    # module_index filters to modules, so doc nodes are ignored), else the persisted graph (a
    # no-change build skips parsing, but episodes may be new).
    code_modules = (
        [node for result in ingest.results.values() for node in result.nodes]
        if ingest.rebuilt
        else graph.nodes_of_kind(scope, "module")
    )

    links = links if links is not None else InMemoryCrossLinkIndex()
    footprints = [
        (episode.ref, tuple(episode.metadata.get("footprint", ()))) for episode in episodes
    ]
    # Footprints link to code modules only (episodes touch source files).
    link_by_footprint(footprints, code_modules, links, repo_root=repo)

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

    # Direct structural retrieval, per corpus: a cue can hit code or docs directly (not only via
    # cross-links), each from its own (now incrementally-maintained) index so they don't pollute.
    structural_retrievers = [
        StructuralRetriever(encoder, spec.index, corpus=spec.corpus) for spec in corpora
    ]

    # §13.18 R1: the un-superseded frontier is a view over the durable SUPERSEDES edges.
    # Read it once at composition; the demoting retriever promotes current truth into the
    # shown slots and the gateway annotates any surfaced superseded belief with its reason.
    supersession = supersession if supersession is not None else InMemorySupersessionIndex()
    superseded = supersession.superseded(scope)

    # One shared, swappable snapshot of the refreshable derived views (superseded frontier +
    # footprint staleness). Both the demoting retriever (promotion) and the Gateway (annotation)
    # read it, so a single dreaming ``gateway.refresh(...)`` reaches both — the long-running-serve
    # refresh seam that replaces the PoC's accidental refresh-on-/mcp-reconnect.
    views_ref = DerivedViewsRef(
        DerivedViews(superseded=superseded, stale_references=stale_references)
    )

    # The relevance retriever: L0 (semantic) alone, or fused with a BM25 lexical leg so exact
    # identifiers/error-strings the vector pool misses still surface (hybrid recall). L0 stays the
    # pristine baseline; hybrid is an ablatable rung behind the same Retriever seam.
    base: Retriever = L0Retriever(encoder, store)
    policy = "L0"
    if hybrid_retrieval:
        base = HybridRetriever(base, LexicalRetriever(store))
        policy = "L0+lexical"
    retriever: Retriever = StructuralLinkedRetriever(base, store, graph, links, k_hop=k_hop)
    retriever = SupersededDemotingRetriever(retriever, views=views_ref)
    if event_sink is not None:
        retriever = LoggingRetriever(
            retriever, event_sink, policy_id=f"{policy}+structural+supersession"
        )
    return Gateway(
        retriever,
        k=k,
        graph=graph,
        links=links,
        structural_retrievers=structural_retrievers,
        structural_k_hop=k_hop,
        structural_min_relevance=structural_min_relevance,
        views=views_ref,
        max_structural_items=max_structural_items,
        max_memory_chars=max_memory_chars,
        usage_sink=usage_sink,
    )


def build_code_graph(
    repo: Path,
    scope: Scope,
    *,
    resolve_calls: bool = True,
    code_language: str = "python",
    scip_index: Path | None = None,
) -> tuple[StructuralGraph, list[StructuralNode]]:
    """Re-derive just the code structural graph (Python AST + jedi, or SCIP; no docs/index).

    The substrate footprint operations need — memory footprints and work footprints both link
    to code module nodes, and k-hop spreads over the code edges. Shared by serve's full
    two-hemisphere build (conceptually) and the footprint usage-attribution pass."""
    result = _code_ingestor(code_language, scip_index, resolve_calls).ingest_path(repo, scope)
    graph: StructuralGraph = InMemoryStructuralGraph(scope)
    graph.add(result)
    return graph, list(result.nodes)


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
