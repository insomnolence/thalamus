"""thalamus.structural — Brain 2: a re-derivable structural graph over a corpus.

**Code is the first corpus** (the Python AST ingestor); other languages
(tree-sitter / SCIP) and document corpora are future ingestors behind the
``Ingestor`` seam, into the same graph. Cross-hemisphere links anchor experiential
memories to structural nodes (§13.19). See ``deep-dives/structural-hemisphere.md``.
"""

from thalamus.structural.cross_link import CrossLinkIndex, InMemoryCrossLinkIndex
from thalamus.structural.graph import Direction, InMemoryStructuralGraph, StructuralGraph
from thalamus.structural.ingestor import Ingestor
from thalamus.structural.python_ast import PythonAstIngestor
from thalamus.structural.schema import (
    IngestResult,
    SourceAnchor,
    StructuralEdge,
    StructuralNode,
)

__all__ = [
    "CrossLinkIndex",
    "Direction",
    "IngestResult",
    "InMemoryCrossLinkIndex",
    "InMemoryStructuralGraph",
    "Ingestor",
    "PythonAstIngestor",
    "SourceAnchor",
    "StructuralEdge",
    "StructuralGraph",
    "StructuralNode",
]
