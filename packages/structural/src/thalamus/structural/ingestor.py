"""The Ingestor seam — corpus-agnostic structural extraction.

Language/format-specific implementations produce typed nodes+edges into the
shared graph: the Python AST ingestor first; tree-sitter / SCIP for other
languages and precise calls; a document ingestor for prose — all behind this one
protocol (``deep-dives/structural-hemisphere.md``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from thalamus.core.types import Scope
from thalamus.structural.schema import IngestResult


@runtime_checkable
class Ingestor(Protocol):
    """Extracts a re-derivable structural graph from a corpus root.

    Deterministic and re-runnable on change. ``root`` may be a single file or a
    directory; the ingestor decides what within it is in-scope.
    """

    def ingest_path(self, root: Path, scope: Scope) -> IngestResult:
        """Parse ``root`` into structural nodes + edges."""
        ...
