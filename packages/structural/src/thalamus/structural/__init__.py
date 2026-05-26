"""thalamus.structural — Brain 2: a re-derivable structural graph over a corpus.

**Code is the first corpus** (the Python AST ingestor); other languages
(tree-sitter / SCIP) and document corpora are future ingestors behind the
``Ingestor`` seam, into the same graph. Cross-hemisphere links anchor experiential
memories to structural nodes (§13.19). See ``deep-dives/structural-hemisphere.md``.
"""

from thalamus.structural.composite import CompositeIngestor
from thalamus.structural.cross_link import CrossLinkIndex, InMemoryCrossLinkIndex
from thalamus.structural.doc_ingestor import DocIngestor
from thalamus.structural.graph import Direction, InMemoryStructuralGraph, StructuralGraph
from thalamus.structural.index import (
    InMemoryStructuralIndex,
    ScoredNode,
    StructuralIndex,
    StructuralRetriever,
    node_text,
)
from thalamus.structural.ingestor import Ingestor
from thalamus.structural.jedi_calls import JediCallIngestor
from thalamus.structural.linking import footprint_staleness, link_by_footprint
from thalamus.structural.neo4j_graph import Neo4jCrossLinkIndex, Neo4jStructuralGraph
from thalamus.structural.python_ast import PythonAstIngestor
from thalamus.structural.schema import (
    IngestResult,
    SourceAnchor,
    StructuralEdge,
    StructuralNode,
)

__all__ = [
    "CompositeIngestor",
    "CrossLinkIndex",
    "Direction",
    "DocIngestor",
    "IngestResult",
    "InMemoryCrossLinkIndex",
    "InMemoryStructuralGraph",
    "InMemoryStructuralIndex",
    "Ingestor",
    "JediCallIngestor",
    "Neo4jCrossLinkIndex",
    "Neo4jStructuralGraph",
    "PythonAstIngestor",
    "ScoredNode",
    "SourceAnchor",
    "StructuralEdge",
    "StructuralGraph",
    "StructuralIndex",
    "StructuralNode",
    "StructuralRetriever",
    "footprint_staleness",
    "link_by_footprint",
    "node_text",
]
