"""Python AST ingestor — the first structural Ingestor.

Stdlib ``ast``, deterministic, zero-dependency. Extracts modules, classes,
functions, and methods with stable qualified ids + source anchors, and
``contains`` / ``inherits`` / ``imports`` edges.

(Referenced from Polynoica's ``PythonCodeIngestor``: kept the stable-id and
line-range lessons; reimplemented into the corpus-agnostic schema, dropped the
GNN node types.)

**Not yet:** resolved ``calls``/``references`` edges (and cross-module inherit/
import resolution). That needs type-aware resolution, which we *delegate* to a
proven tool — jedi (Python, in-process), then SCIP for precision + multi-language
— rather than hand-roll a worse type-checker (see deep-dives/structural-hemisphere.md
and the §4/§5 deterministic-tools discipline). Inheritance is resolved to
same-module bases only for now; raw base names are kept in node metadata.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from thalamus.structural.schema import (
    IngestResult,
    SourceAnchor,
    StructuralEdge,
    StructuralNode,
)

logger = logging.getLogger(__name__)


def _dotted(path: Path, root_package: str | None) -> str:
    if root_package is None:
        # No package root: use the file stem (the parent dir name for __init__).
        return path.parent.name if path.stem == "__init__" else path.stem
    parts = list(path.with_suffix("").parts)
    pkg = root_package.split(".")
    for i in range(len(parts)):
        if parts[i : i + len(pkg)] == pkg:
            parts = parts[i:]
            break
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _anchor(path: Path, node: ast.AST) -> SourceAnchor:
    line_start = int(getattr(node, "lineno", 1))
    line_end = int(getattr(node, "end_lineno", None) or line_start)
    return SourceAnchor(path=str(path), line_start=line_start, line_end=line_end)


class PythonAstIngestor:
    """Ingests Python source into the structural graph (containment/inherit/imports)."""

    def __init__(self, root_package: str | None = None) -> None:
        self._root_package = root_package

    def ingest_path(self, root: Path) -> IngestResult:
        nodes: list[StructuralNode] = []
        edges: list[StructuralEdge] = []
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in files:
            self._ingest_file(path, nodes, edges)
        return IngestResult(nodes=nodes, edges=edges)

    def _ingest_file(
        self, path: Path, nodes: list[StructuralNode], edges: list[StructuralEdge]
    ) -> None:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("skipping %s: %s", path, exc)
            return
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            logger.warning("syntax error, skipping %s", path)
            return

        module = _dotted(path, self._root_package)
        module_id = f"module:{module}"
        nodes.append(
            StructuralNode(
                node_id=module_id,
                kind="module",
                label=module,
                anchor=SourceAnchor(path=str(path), line_start=1, line_end=source.count("\n") + 1),
            )
        )
        for child in ast.iter_child_nodes(tree):
            if isinstance(child, ast.ClassDef):
                self._add_class(path, child, module, module_id, nodes, edges)
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                func_id = f"function:{module}.{child.name}"
                nodes.append(
                    StructuralNode(
                        node_id=func_id,
                        kind="function",
                        label=f"{module}.{child.name}",
                        anchor=_anchor(path, child),
                    )
                )
                edges.append(StructuralEdge(module_id, func_id, "contains"))
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    edges.append(StructuralEdge(module_id, f"module:{alias.name}", "imports"))
            elif isinstance(child, ast.ImportFrom) and child.module:
                edges.append(StructuralEdge(module_id, f"module:{child.module}", "imports"))

    def _add_class(
        self,
        path: Path,
        class_node: ast.ClassDef,
        module: str,
        module_id: str,
        nodes: list[StructuralNode],
        edges: list[StructuralEdge],
    ) -> None:
        class_id = f"class:{module}.{class_node.name}"
        base_names = [base.id for base in class_node.bases if isinstance(base, ast.Name)]
        nodes.append(
            StructuralNode(
                node_id=class_id,
                kind="class",
                label=f"{module}.{class_node.name}",
                anchor=_anchor(path, class_node),
                metadata={"bases": base_names},
            )
        )
        edges.append(StructuralEdge(module_id, class_id, "contains"))
        for base in base_names:
            # Same-module resolution only; cross-module bases are deferred to jedi/SCIP.
            edges.append(StructuralEdge(class_id, f"class:{module}.{base}", "inherits"))
        for item in class_node.body:
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                method_id = f"method:{module}.{class_node.name}.{item.name}"
                nodes.append(
                    StructuralNode(
                        node_id=method_id,
                        kind="method",
                        label=f"{module}.{class_node.name}.{item.name}",
                        anchor=_anchor(path, item),
                    )
                )
                edges.append(StructuralEdge(class_id, method_id, "contains"))
