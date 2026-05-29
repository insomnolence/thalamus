"""SCIP ingestor — a language-agnostic structural pass behind the ``Ingestor`` seam.

Consumes a pre-built `.scip` index (SCIP Code Intelligence Protocol — protobuf) produced
**out-of-band** by a per-language indexer (e.g. ``scip-typescript`` → see
``scripts/scip-index-typescript.sh``), and maps it to the corpus-agnostic
``StructuralNode``/``StructuralEdge`` schema. One ingestor serves every SCIP language —
TypeScript today, ``scip-python``/Go/… later, with no new ingestor code.

Design notes (empirical, from ``scip-typescript`` 0.4.0):

- ``SymbolInformation.kind`` is **not populated** by scip-typescript, so node kind is
  inferred from the symbol's descriptor suffix (``scip_symbol``) + the leading line of
  its ``documentation`` (``interface``/``class``/``enum`` keyword).
- Node ids stay **path-derivable** for modules (``ids.module_dotted`` over the corpus
  root), so cross-hemisphere footprint linking keeps working for any language.
- ``Metadata.project_root`` is a machine-absolute ``file://`` URI baked into the index,
  so paths are derived from the caller-provided ``root`` (position-independent); a
  cheap exists-on-disk check guards against a wrong ``--repo``.

Edge resolution (``calls``/``implements``/``contains``) lives in ``_build_edges``.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from thalamus.core.exceptions import ThalamusError
from thalamus.core.types import Scope
from thalamus.structural.ids import module_id
from thalamus.structural.schema import IngestResult, SourceAnchor, StructuralEdge, StructuralNode
from thalamus.structural.scip_symbol import Descriptor, Suffix, parse_symbol

if TYPE_CHECKING:
    from collections.abc import Sequence

# SCIP SymbolRole bitset — Definition is bit 0x1 (scip.proto). A reference occurrence
# carries no Definition bit; that is how the call resolver tells uses from defs.
_DEFINITION = 0x1

_MAX_TEXT_CHARS = 600


class ScipIngestor:
    """Ingests a SCIP index file into the structural graph.

    ``index_path`` points at a `.scip` artifact built out-of-band. ``root_package``
    optionally prefixes module dotted-names (mirrors ``PythonAstIngestor``).
    """

    def __init__(self, index_path: Path, *, root_package: str | None = None) -> None:
        self._index_path = Path(index_path)
        self._root_package = root_package

    def ingest_path(self, root: Path, scope: Scope) -> IngestResult:
        index = self._load_index()
        self._check_root(root, index)
        nodes, symbol_to_id = self._build_nodes(index, root, scope)
        kind_by_id = {n.node_id: n.kind for n in nodes}
        edges = self._build_edges(index, root, symbol_to_id, kind_by_id)
        return IngestResult(nodes=nodes, edges=edges)

    # -- loading -----------------------------------------------------------------

    def _load_index(self) -> Any:
        # Imported dynamically (typed Any) so the generated protobuf bindings stay an opaque
        # boundary — no static dependency on their dynamically-built ``Index`` attribute.
        try:
            scip_pb2: Any = importlib.import_module("thalamus.structural._scip.scip_pb2")
        except ImportError as exc:  # pragma: no cover - exercised via the composition guard
            raise ThalamusError(
                "ScipIngestor needs the 'scip' extra: install thalamus-structural[scip]"
            ) from exc
        if not self._index_path.exists():
            raise ThalamusError(f"SCIP index not found: {self._index_path}")
        index = scip_pb2.Index()
        index.ParseFromString(self._index_path.read_bytes())
        return index

    def _check_root(self, root: Path, index: Any) -> None:
        """Guard against a wrong ``--repo``: the indexed files must exist under ``root``.

        Relocation-safe (does not compare the baked-in absolute ``project_root``)."""
        if not index.documents:
            return
        rel = index.documents[0].relative_path
        if not (root / Path(rel)).exists():
            hint = _strip_file_uri(index.metadata.project_root)
            raise ThalamusError(
                f"--repo ({root}) does not contain the SCIP-indexed files (e.g. {rel!r}); "
                f"point it at the indexer's project root (was {hint!r})"
            )

    # -- nodes -------------------------------------------------------------------

    def _build_nodes(
        self, index: Any, root: Path, scope: Scope
    ) -> tuple[list[StructuralNode], dict[str, str]]:
        nodes: list[StructuralNode] = []
        symbol_to_id: dict[str, str] = {}

        for doc in index.documents:
            rel = doc.relative_path
            abs_path = str(root / Path(rel))
            dotted = self._dotted(root / Path(rel), root)
            mod_id = module_id(dotted)
            defs = {occ.symbol: occ for occ in doc.occurrences if occ.symbol_roles & _DEFINITION}

            n_path = len(Path(rel).parts)
            module_seen = False
            for sym in doc.symbols:
                parsed = parse_symbol(sym.symbol)
                if parsed.is_local:
                    continue
                remainder = parsed.descriptors[n_path:]
                doc_text = _doc_text(sym.documentation)
                kind = _node_kind(remainder, doc_text)
                if kind is None:
                    continue
                if kind == "module":
                    node_id, label = mod_id, dotted
                    module_seen = True
                else:
                    qualified = ".".join(d.name for d in remainder)
                    node_id = f"{kind}:{dotted}.{qualified}"
                    label = f"{dotted}.{qualified}"
                anchor = _anchor(abs_path, defs.get(sym.symbol))
                nodes.append(
                    StructuralNode(
                        node_id=node_id,
                        kind=kind,
                        label=label,
                        scope=scope,
                        anchor=anchor,
                        metadata={"text": _embeddable_text(kind, label, doc_text)},
                    )
                )
                symbol_to_id[sym.symbol] = node_id

            if not module_seen:  # defensive: synthesize a module node if the index omits one
                anchor = SourceAnchor(abs_path, 1, 1)
                nodes.append(StructuralNode(mod_id, "module", dotted, scope, anchor, {}))

        return nodes, symbol_to_id

    def _dotted(self, abs_path: Path, root: Path) -> str:
        from thalamus.structural.ids import module_dotted

        return module_dotted(abs_path, root, self._root_package)

    # -- edges --------------------------------------------------------------------

    def _build_edges(
        self, index: Any, root: Path, symbol_to_id: dict[str, str], kind_by_id: dict[str, str]
    ) -> list[StructuralEdge]:
        """``contains`` (descriptor hierarchy), ``calls`` (enclosing-range), and
        ``implements``/``inherits`` (relationships)."""
        edges: list[StructuralEdge] = []
        edges.extend(self._contains_edges(index, root, symbol_to_id))
        edges.extend(self._call_edges(index, symbol_to_id, kind_by_id))
        edges.extend(self._inheritance_edges(index, symbol_to_id, kind_by_id))
        return edges

    def _contains_edges(
        self, index: Any, root: Path, symbol_to_id: dict[str, str]
    ) -> list[StructuralEdge]:
        """module ⊃ class/function, class ⊃ method — from the descriptor hierarchy.

        (scip-typescript leaves ``enclosing_symbol`` unset, so containment is derived
        structurally: a symbol's parent is the same symbol minus its last descriptor.)
        """
        edges: list[StructuralEdge] = []
        for doc in index.documents:
            n_path = len(Path(doc.relative_path).parts)
            mod_id = module_id(self._dotted(root / Path(doc.relative_path), root))
            by_chain: dict[tuple[str, ...], str] = {}
            for sym in doc.symbols:
                node_id = symbol_to_id.get(sym.symbol)
                if node_id is None:
                    continue
                parsed = parse_symbol(sym.symbol)
                by_chain[tuple(d.name for d in parsed.descriptors[n_path:])] = node_id
            for chain, node_id in by_chain.items():
                if not chain:  # the module itself
                    continue
                parent = mod_id if len(chain) == 1 else by_chain.get(chain[:-1])
                if parent is not None and parent != node_id:
                    edges.append(StructuralEdge(parent, node_id, "contains"))
        return edges

    def _call_edges(
        self, index: Any, symbol_to_id: dict[str, str], kind_by_id: dict[str, str]
    ) -> list[StructuralEdge]:
        """A reference to a callable, attributed to the innermost enclosing callable def.

        Requiring an enclosing *callable* caller naturally drops import/type-position
        references (which sit only inside a module/class), so the Import bit isn't needed.
        """
        edges: list[StructuralEdge] = []
        seen: set[tuple[str, str]] = set()
        for doc in index.documents:
            callable_defs: list[tuple[str, tuple[int, int, int, int]]] = []
            for occ in doc.occurrences:
                if not (occ.symbol_roles & _DEFINITION) or not occ.enclosing_range:
                    continue
                node_id = symbol_to_id.get(occ.symbol)
                if node_id is not None and kind_by_id.get(node_id) in ("function", "method"):
                    callable_defs.append((node_id, _range_bounds(list(occ.enclosing_range))))
            for occ in doc.occurrences:
                if occ.symbol_roles & _DEFINITION:
                    continue  # references only
                callee = symbol_to_id.get(occ.symbol)
                if callee is None or kind_by_id.get(callee) not in ("function", "method"):
                    continue
                caller = _innermost(callable_defs, _range_start(list(occ.range)))
                if caller is not None and caller != callee and (caller, callee) not in seen:
                    seen.add((caller, callee))
                    edges.append(StructuralEdge(caller, callee, "calls"))
        return edges

    def _inheritance_edges(
        self, index: Any, symbol_to_id: dict[str, str], kind_by_id: dict[str, str]
    ) -> list[StructuralEdge]:
        edges: list[StructuralEdge] = []
        for doc in index.documents:
            for sym in doc.symbols:
                source = symbol_to_id.get(sym.symbol)
                if source is None:
                    continue
                for rel in sym.relationships:
                    if not rel.is_implementation:
                        continue
                    target = symbol_to_id.get(rel.symbol)
                    if target is None or target == source:
                        continue
                    target_kind = kind_by_id.get(target)
                    if target_kind == "interface":
                        edges.append(StructuralEdge(source, target, "implements"))
                    elif target_kind == "class":
                        edges.append(StructuralEdge(source, target, "inherits"))
        return edges


def _strip_file_uri(value: str) -> str:
    return value[len("file://") :] if value.startswith("file://") else value


def _doc_text(documentation: Sequence[str]) -> str:
    """Flatten SymbolInformation.documentation, stripping markdown code fences."""
    text = "\n".join(documentation).strip()
    return text.replace("```ts", "").replace("```", "").strip()


def _node_kind(remainder: tuple[Descriptor, ...], doc_text: str) -> str | None:
    """Open-vocabulary kind for a symbol, or ``None`` to skip (term/parameter/…).

    scip-typescript omits ``SymbolInformation.kind``, so derive it from the descriptor
    suffix; for a type, refine class/interface/enum from the documentation keyword.
    """
    if not remainder:
        return "module"
    last = remainder[-1]
    if last.suffix is Suffix.METHOD:
        encloses_type = any(d.suffix is Suffix.TYPE for d in remainder[:-1])
        return "method" if encloses_type else "function"
    if last.suffix is Suffix.TYPE:
        lowered = doc_text.lower()
        if "interface " in lowered:
            return "interface"
        if "enum " in lowered:
            return "enum"
        return "class"
    return None  # term / parameter / type-parameter / namespace — not a structural node


def _embeddable_text(kind: str, label: str, doc_text: str) -> str:
    parts = [f"{kind} {label}"]
    if doc_text:
        parts.append(doc_text)
    return "\n".join(parts)[:_MAX_TEXT_CHARS]


def _anchor(path: str, occ: Any | None) -> SourceAnchor:
    if occ is None:
        return SourceAnchor(path, 1, 1)
    span = list(occ.enclosing_range) or list(occ.range)
    start, end = _line_span(span)
    return SourceAnchor(path, start, end)


def _line_span(rng: list[int]) -> tuple[int, int]:
    """SCIP range (0-based) → 1-based (line_start, line_end).

    A range is ``[startLine, startChar, endLine, endChar]`` (4) or, for a single line,
    ``[line, startChar, endChar]`` (3)."""
    if len(rng) >= 4:
        return rng[0] + 1, rng[2] + 1
    if len(rng) == 3:
        return rng[0] + 1, rng[0] + 1
    return 1, 1


def _range_bounds(rng: list[int]) -> tuple[int, int, int, int]:
    """(start_line, start_char, end_line, end_char) — 0-based, both range forms."""
    if len(rng) >= 4:
        return rng[0], rng[1], rng[2], rng[3]
    if len(rng) == 3:
        return rng[0], rng[1], rng[0], rng[2]
    return 0, 0, 0, 0


def _range_start(rng: list[int]) -> tuple[int, int]:
    return (rng[0], rng[1]) if len(rng) >= 2 else (0, 0)


def _innermost(
    defs: list[tuple[str, tuple[int, int, int, int]]], pos: tuple[int, int]
) -> str | None:
    """The node whose enclosing range contains ``pos`` and starts latest (the tightest)."""
    line, char = pos
    best: str | None = None
    best_start: tuple[int, int] | None = None
    for node_id, (sl, sc, el, ec) in defs:
        after = line > sl or (line == sl and char >= sc)
        before = line < el or (line == el and char <= ec)
        if after and before and (best_start is None or (sl, sc) > best_start):
            best, best_start = node_id, (sl, sc)
    return best
