"""Resolved ``calls`` edges via jedi — the Brain-2 call graph.

The AST ingestor gives structure (contains / inherits / imports); this resolves
*who calls whom*. Call resolution needs type inference, so we **delegate to jedi** — a
proven resolver — rather than hand-roll a worse type-checker (the §4/§5 deterministic-
tools discipline). jedi is the interim Python resolver; SCIP (precise, multi-language)
is the later path behind this same ``Ingestor`` seam.

Resolved definitions map back to our canonical node ids by **(file, line)** — robust to
src-layouts where the import name differs from the file-path-derived id. Calls jedi
cannot resolve, or that land outside the corpus (stdlib / third-party), produce no
edge. The scope model mirrors the AST ingestor's (module / top-level function /
class / method); a call attributes to its nearest such enclosing owner.

``jedi`` is an optional dependency (the ``jedi`` extra); the import is deferred so the
package loads without it.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thalamus.core.exceptions import ThalamusError
from thalamus.core.types import Scope
from thalamus.structural.ids import class_id, function_id, method_id, module_dotted, module_id
from thalamus.structural.schema import IngestResult, StructuralEdge
from thalamus.structural.sources import IGNORE_DIRS, python_files

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _CallSite:
    """A call to resolve: the position of the *called name* and who is calling."""

    caller_id: str
    line: int  # 1-based line of the called name
    column: int  # 0-based column of the called name


class JediCallIngestor:
    """Ingestor emitting resolved ``calls`` edges between canonical node ids.

    Requires the ``jedi`` extra; raises a clear error if used without it.
    """

    def __init__(
        self, root_package: str | None = None, *, ignore_dirs: frozenset[str] = IGNORE_DIRS
    ) -> None:
        self._root_package = root_package
        self._ignore_dirs = ignore_dirs

    def ingest_path(self, root: Path, scope: Scope) -> IngestResult:
        try:
            import jedi
        except ImportError as exc:  # pragma: no cover - exercised via the composition guard
            raise ThalamusError(
                "JediCallIngestor needs the 'jedi' extra: install thalamus-structural[jedi]"
            ) from exc

        # One AST pass per file: build the (file, line) -> node id index for *all* files
        # (so cross-module calls resolve), and collect call sites with their caller.
        def_index: dict[tuple[str, int], str] = {}
        calls_by_file: dict[str, list[_CallSite]] = {}
        for path in python_files(root, self._ignore_dirs):
            tree = self._parse(path)
            if tree is None:
                continue
            abspath = str(path.resolve())
            sites: list[_CallSite] = []
            self._index(
                tree,
                module=module_dotted(path, root, self._root_package),
                abspath=abspath,
                owner_id=module_id(module_dotted(path, root, self._root_package)),
                parent="module",
                class_name=None,
                def_index=def_index,
                sites=sites,
            )
            calls_by_file[abspath] = sites

        project = jedi.Project(str(root if root.is_dir() else root.parent))
        edges: list[StructuralEdge] = []
        seen: set[tuple[str, str]] = set()
        for abspath, sites in calls_by_file.items():
            if not sites:
                continue
            try:
                script = jedi.Script(path=abspath, project=project)
            except Exception as exc:  # a file jedi can't load must not abort the whole ingest
                logger.warning("jedi could not load %s: %s", abspath, exc)
                continue
            for site in sites:
                for target_id in self._resolve(script, site, def_index):
                    key = (site.caller_id, target_id)
                    if target_id != site.caller_id and key not in seen:
                        seen.add(key)
                        edges.append(StructuralEdge(site.caller_id, target_id, "calls"))
        return IngestResult(nodes=[], edges=edges)

    @staticmethod
    def _parse(path: Path) -> ast.AST | None:
        try:
            return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            logger.warning("skipping %s: %s", path, exc)
            return None

    def _index(
        self,
        node: ast.AST,
        *,
        module: str,
        abspath: str,
        owner_id: str,
        parent: str,
        class_name: str | None,
        def_index: dict[tuple[str, int], str],
        sites: list[_CallSite],
    ) -> None:
        """Walk the tree, indexing defs (mirroring the AST ingestor's shallow model)
        and collecting call sites attributed to the nearest indexed owner."""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef) and parent == "module":
                cid = class_id(module, child.name)
                def_index[(abspath, child.lineno)] = cid
                self._index(
                    child, module=module, abspath=abspath, owner_id=cid, parent="class",
                    class_name=child.name, def_index=def_index, sites=sites,
                )
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and parent in (
                "module",
                "class",
            ):
                fid = (
                    method_id(module, class_name, child.name)
                    if parent == "class" and class_name is not None
                    else function_id(module, child.name)
                )
                def_index[(abspath, child.lineno)] = fid
                self._index(
                    child, module=module, abspath=abspath, owner_id=fid, parent="body",
                    class_name=None, def_index=def_index, sites=sites,
                )
            else:
                if isinstance(child, ast.Call):
                    site = self._call_site(child, owner_id)
                    if site is not None:
                        sites.append(site)
                self._index(
                    child, module=module, abspath=abspath, owner_id=owner_id, parent="body",
                    class_name=class_name, def_index=def_index, sites=sites,
                )

    @staticmethod
    def _call_site(call: ast.Call, owner_id: str) -> _CallSite | None:
        """Position of the called name (``foo`` in ``foo()``; ``bar`` in ``x.bar()``)."""
        func = call.func
        if isinstance(func, ast.Name):
            return _CallSite(owner_id, func.lineno, func.col_offset)
        if (
            isinstance(func, ast.Attribute)
            and func.end_lineno is not None
            and func.end_col_offset is not None
        ):
            column = max(func.end_col_offset - len(func.attr), 0)
            return _CallSite(owner_id, func.end_lineno, column)
        return None  # subscript/other call expressions: not resolved

    @staticmethod
    def _resolve(script: Any, site: _CallSite, def_index: dict[tuple[str, int], str]) -> list[str]:
        try:
            definitions = script.goto(
                site.line, site.column, follow_imports=True, follow_builtin_imports=False
            )
        except Exception:  # jedi can raise on odd positions — treat as unresolved
            return []
        targets: list[str] = []
        for definition in definitions:
            module_path = definition.module_path
            if module_path is None or definition.line is None:
                continue
            target = def_index.get((str(Path(module_path).resolve()), definition.line))
            if target is not None:
                targets.append(target)
        return targets
