"""thalamus.structural — Brain 2: a re-derivable structural graph over a corpus.

**Code is the first corpus** (the Python AST ingestor); other languages
(tree-sitter / SCIP) and document corpora are future ingestors behind the
``Ingestor`` seam, into the same graph. Cross-hemisphere links anchor experiential
memories to structural nodes (§13.19). See ``deep-dives/structural-hemisphere.md``.
"""

from thalamus.structural.anchoring import (
    anchor_nodes,
    linked_nodes_for,
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
from thalamus.structural.linking import footprint_staleness, link_by_footprint, module_index
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
from thalamus.structural.text_ingestor import (
    DEFAULT_CHUNK_CHARS,
    DEFAULT_OVERLAP_CHARS,
    TextIngestor,
    chunk_lines,
)

__all__ = [
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
    "TextIngestor",
    "UsageAttributor",
    "anchor_nodes",
    "chunk_lines",
    "code_files",
    "glob_files",
    "footprint_staleness",
    "incremental_ingest",
    "link_by_footprint",
    "linked_nodes_for",
    "markdown_files",
    "module_index",
    "node_text",
    "python_files",
    "ranked_hits",
    "resolve_and_expand",
    "text_files",
    "typescript_files",
]
