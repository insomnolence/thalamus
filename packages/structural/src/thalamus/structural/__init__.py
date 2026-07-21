"""thalamus.structural — Brain 2: a re-derivable structural graph over a corpus.

Built-in ingestors: Python AST (``PythonAstIngestor`` + jedi calls), multi-language
SCIP (``ScipIngestor``), Markdown docs (``DocIngestor``), plain text (``TextIngestor``),
and external analysis findings (``FindingsIngestor``) — each behind the ``Ingestor``
seam, into the same shared graph with per-corpus vector indexes. A lightweight tree-sitter
ingestor for languages without a SCIP indexer is the remaining future ingestor. Cross-hemisphere
links anchor experiential memories to structural nodes (§13.19). Non-code nodes are linked
to the code they annotate via ``annotates`` edges (``anchor_linking``). See
``deep-dives/structural-hemisphere.md``.
"""

from thalamus.structural.anchor_linking import (
    ANNOTATES,
    AnnotationLocator,
    default_annotation_location,
    link_anchored_nodes,
)
from thalamus.structural.anchoring import (
    anchor_nodes,
    linked_nodes_for,
    memory_centrality,
    node_degree,
    ranked_hits,
    resolve_and_expand,
)
from thalamus.structural.attribution import (
    AttributedUse,
    FootprintAttributor,
    ShownMemory,
    UsageAttributor,
)
from thalamus.structural.cochange import (
    CoChangeIndex,
    CoChangeRef,
    FileCoChangeIndex,
    InMemoryCoChangeIndex,
)
from thalamus.structural.composite import CompositeIngestor
from thalamus.structural.cross_link import CrossLinkIndex, InMemoryCrossLinkIndex
from thalamus.structural.doc_ingestor import DocIngestor
from thalamus.structural.findings_ingestor import (
    Finding,
    FindingsIngestor,
    findings_files,
    parse_findings,
)
from thalamus.structural.graph import Direction, InMemoryStructuralGraph, StructuralGraph
from thalamus.structural.incremental import (
    CorpusSpec,
    IncrementalResult,
    IngestStats,
    incremental_ingest,
)
from thalamus.structural.index import (
    InMemoryStructuralIndex,
    ScoredNode,
    StructuralIndex,
    StructuralRetriever,
    node_text,
)
from thalamus.structural.ingestor import Ingestor
from thalamus.structural.jedi_calls import JediCallIngestor
from thalamus.structural.linking import (
    FootprintFile,
    footprint_from_metadata,
    footprint_staleness,
    link_by_footprint,
    module_index,
)
from thalamus.structural.manifest import (
    FileManifest,
    InMemoryFileManifest,
    ManifestEntry,
    Neo4jFileManifest,
)
from thalamus.structural.neo4j_graph import Neo4jCrossLinkIndex, Neo4jStructuralGraph
from thalamus.structural.neo4j_index import Neo4jStructuralIndex
from thalamus.structural.python_ast import PythonAstIngestor
from thalamus.structural.schema import (
    IngestResult,
    SourceAnchor,
    StructuralEdge,
    StructuralNode,
)
from thalamus.structural.scip_ingestor import ScipIngestor
from thalamus.structural.sources import (
    code_files,
    glob_files,
    markdown_files,
    python_files,
    text_files,
    typescript_files,
)
from thalamus.structural.symbol_resolution import SymbolResolver
from thalamus.structural.text_ingestor import (
    DEFAULT_CHUNK_CHARS,
    DEFAULT_OVERLAP_CHARS,
    TextIngestor,
    chunk_lines,
)
from thalamus.structural.trust_stamp import TrustStampingIngestor

__all__ = [
    "ANNOTATES",
    "AnnotationLocator",
    "AttributedUse",
    "CoChangeIndex",
    "CoChangeRef",
    "CompositeIngestor",
    "CorpusSpec",
    "CrossLinkIndex",
    "DEFAULT_CHUNK_CHARS",
    "DEFAULT_OVERLAP_CHARS",
    "Direction",
    "DocIngestor",
    "FileCoChangeIndex",
    "FileManifest",
    "Finding",
    "FootprintFile",
    "FindingsIngestor",
    "FootprintAttributor",
    "IncrementalResult",
    "InMemoryFileManifest",
    "IngestResult",
    "IngestStats",
    "InMemoryCoChangeIndex",
    "InMemoryCrossLinkIndex",
    "InMemoryStructuralGraph",
    "InMemoryStructuralIndex",
    "Ingestor",
    "JediCallIngestor",
    "ManifestEntry",
    "Neo4jCrossLinkIndex",
    "Neo4jFileManifest",
    "Neo4jStructuralGraph",
    "Neo4jStructuralIndex",
    "PythonAstIngestor",
    "ScipIngestor",
    "ScoredNode",
    "ShownMemory",
    "SourceAnchor",
    "StructuralEdge",
    "StructuralGraph",
    "StructuralIndex",
    "StructuralNode",
    "StructuralRetriever",
    "SymbolResolver",
    "TextIngestor",
    "TrustStampingIngestor",
    "UsageAttributor",
    "anchor_nodes",
    "chunk_lines",
    "code_files",
    "default_annotation_location",
    "glob_files",
    "footprint_from_metadata",
    "footprint_staleness",
    "findings_files",
    "incremental_ingest",
    "link_anchored_nodes",
    "link_by_footprint",
    "linked_nodes_for",
    "markdown_files",
    "memory_centrality",
    "module_index",
    "node_degree",
    "node_text",
    "parse_findings",
    "python_files",
    "ranked_hits",
    "resolve_and_expand",
    "text_files",
    "typescript_files",
]
