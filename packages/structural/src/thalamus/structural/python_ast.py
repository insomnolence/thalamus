"""Python AST ingestor — the first structural Ingestor.

Stdlib ``ast``, deterministic, zero-dependency. Extracts modules, classes,
functions, and methods with stable qualified ids + source anchors, and
``contains`` / ``inherits`` / ``imports`` edges.

(Referenced from an earlier project of ours: kept the stable-id and
line-range lessons; reimplemented into the corpus-agnostic schema, dropped the
model-specific node types.)

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

from thalamus.core.types import Scope
from thalamus.structural.ids import class_id, function_id, method_id, module_dotted, module_id
from thalamus.structural.schema import (
    IngestResult,
    SourceAnchor,
    StructuralEdge,
    StructuralNode,
)
from thalamus.structural.sources import IGNORE_DIRS, python_files

logger = logging.getLogger(__name__)


def _anchor(path: Path, node: ast.AST) -> SourceAnchor:
    line_start = int(getattr(node, "lineno", 1))
    line_end = int(getattr(node, "end_lineno", None) or line_start)
    return SourceAnchor(path=str(path), line_start=line_start, line_end=line_end)


class PythonAstIngestor:
    """Ingests Python source into the structural graph (containment/inherit/imports)."""

    def __init__(
        self, root_package: str | None = None, *, ignore_dirs: frozenset[str] = IGNORE_DIRS
    ) -> None:
        self._root_package = root_package
        self._ignore_dirs = ignore_dirs

    def ingest_path(self, root: Path, scope: Scope) -> IngestResult:
        nodes: list[StructuralNode] = []
        edges: list[StructuralEdge] = []
        for path in python_files(root, self._ignore_dirs):
            self._ingest_file(path, root, scope, nodes, edges)
        return IngestResult(nodes=nodes, edges=edges)

    def _ingest_file(
        self,
        path: Path,
        root: Path,
        scope: Scope,
        nodes: list[StructuralNode],
        edges: list[StructuralEdge],
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

        module = module_dotted(path, root, self._root_package)
        mod_id = module_id(module)
        nodes.append(
            StructuralNode(
                node_id=mod_id,
                kind="module",
                label=module,
                scope=scope,
                anchor=SourceAnchor(path=str(path), line_start=1, line_end=source.count("\n") + 1),
            )
        )
        for child in ast.iter_child_nodes(tree):
            if isinstance(child, ast.ClassDef):
                self._add_class(path, child, module, mod_id, scope, nodes, edges)
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                func_id = function_id(module, child.name)
                nodes.append(
                    StructuralNode(
                        node_id=func_id,
                        kind="function",
                        label=f"{module}.{child.name}",
                        scope=scope,
                        anchor=_anchor(path, child),
                    )
                )
                edges.append(StructuralEdge(mod_id, func_id, "contains"))
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    edges.append(StructuralEdge(mod_id, module_id(alias.name), "imports"))
            elif isinstance(child, ast.ImportFrom) and child.module:
                edges.append(StructuralEdge(mod_id, module_id(child.module), "imports"))

    def _add_class(
        self,
        path: Path,
        class_node: ast.ClassDef,
        module: str,
        mod_id: str,
        scope: Scope,
        nodes: list[StructuralNode],
        edges: list[StructuralEdge],
    ) -> None:
        cls_id = class_id(module, class_node.name)
        base_names = [base.id for base in class_node.bases if isinstance(base, ast.Name)]
        nodes.append(
            StructuralNode(
                node_id=cls_id,
                kind="class",
                label=f"{module}.{class_node.name}",
                scope=scope,
                anchor=_anchor(path, class_node),
                metadata={"bases": base_names},
            )
        )
        edges.append(StructuralEdge(mod_id, cls_id, "contains"))
        for base in base_names:
            # Same-module resolution only; cross-module bases are deferred to jedi/SCIP.
            edges.append(StructuralEdge(cls_id, class_id(module, base), "inherits"))
        for item in class_node.body:
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                meth_id = method_id(module, class_node.name, item.name)
                nodes.append(
                    StructuralNode(
                        node_id=meth_id,
                        kind="method",
                        label=f"{module}.{class_node.name}.{item.name}",
                        scope=scope,
                        anchor=_anchor(path, item),
                    )
                )
                edges.append(StructuralEdge(cls_id, meth_id, "contains"))
