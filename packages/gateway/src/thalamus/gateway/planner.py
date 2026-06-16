"""The plan / impact tool — a blast-radius brief for a change (C-3).

Given a *target* (a symbol name or NL description of where a change will land), the
:class:`Planner` resolves it to a Brain-2 code node, computes a bounded blast radius
(what calls it = "what breaks", what it uses, what contains it), gathers the *why* the
brain has recorded about everything in scope (cross-hemisphere links → decisions /
constraints / gotchas), and assembles one structured :class:`PlanBrief` — so the
actuator walks into the task already holding the whole-system picture (§16, planning.md).

**Deterministic core, by design (§14.2).** Resolution (semantic match), radius (graph
traversal), and gather (cross-link join + store reads) are fully deterministic — the brief
reproduces from the same graph state. No LLM synthesis here; a natural-language summary is a
*later, removable* layer on top of this structured payload, deliberately deferred until the
core is measured (see the refined design — git-derived blast-radius recall as the eval).

**Coverage honesty is the load-bearing property.** Memory coverage over code is sparse, so a
brief that renders an empty section is only safe if it distinguishes "the brain *knows* there
is nothing here" from "the brain has *no data* here" — otherwise it makes the actuator bolder
exactly where we are blind. Every brief therefore carries a :class:`CoverageReport` and
per-node link counts; absence is always reported as *unverified*.

**Bounded, not exhaustive.** Reverse reachability over a real call graph is enormous, so the
radius is a budgeted, edge-typed frontier, and a high-fan-out *hub* node (shared
infrastructure) trips a circuit-breaker: the brief refuses to enumerate hundreds of call-sites
and instead flags "broad blast radius — change with a compat strategy", which is the more
useful thing to tell the actuator.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from thalamus.core.protocols import Store
from thalamus.core.types import Cue, MemoryId, Scope, ScoredMemory, StructuralRef
from thalamus.gateway.payload import MemoryItem, StructuralItem
from thalamus.gateway.views import DerivedViewsRef
from thalamus.structural import (
    CoChangeIndex,
    CrossLinkIndex,
    StructuralGraph,
    StructuralNode,
    StructuralRetriever,
    ranked_hits,
)

# Memory kinds that count as a load-bearing *constraint* (vs. general decision/context).
_CONSTRAINT_KINDS = frozenset({"constraint", "gotcha"})

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")
# Symbol kinds the full-graph exact-name lookup considers; types rank ahead of callables when a
# token matches several, since a descriptive target usually names the type it's about.
_CODE_KINDS = ("interface", "class", "enum", "function", "method")
_KIND_RANK = {kind: rank for rank, kind in enumerate(_CODE_KINDS)}


def _identifier_tokens(text: str) -> list[str]:
    """Code-identifier-looking tokens in a target (CamelCase / snake_case), in first-seen order —
    the prose words a descriptive target wraps a symbol in ("...where IntegrationService dispatches
    ...") are dropped, so an exact symbol-name match can outrank a semantic (doc) hit."""
    out: list[str] = []
    seen: set[str] = set()
    for token in _IDENTIFIER.findall(text):
        lowered = token.lower()
        identifier_like = any(c.isupper() for c in token[1:]) or "_" in token
        if len(token) >= 3 and lowered not in seen and identifier_like:
            seen.add(lowered)
            out.append(lowered)
    return out


def _simple_name(label: str) -> str:
    """The last dotted segment of a node label — its bare symbol name."""
    return label.rsplit(".", 1)[-1]


# Walking ``contains`` up from a symbol to its module: a small bound guards against a cyclic or
# pathologically deep nesting chain (module → class → method is depth 2 in practice).
_MAX_CONTAINER_DEPTH = 6
_TEST_DIR_SEGMENTS = frozenset({"test", "tests", "__tests__", "spec"})


def _is_test_path(path: str) -> bool:
    """True for conventional test files in any language — ``tests/`` dirs, ``test_*`` / ``*_test``
    stems, ``*.test.*`` / ``*.spec.*`` names.

    Test-mirror co-change ("change a symbol → its own test file changes") is near-zero signal for
    a blast radius — it's the trivial coupling, not the cross-cutting kind the layer exists to
    surface — so co-change filters it out (removable via ``PlannerConfig.cochange_skip_tests``)."""
    p = path.replace("\\", "/")
    parts = p.split("/")
    if any(seg in _TEST_DIR_SEGMENTS for seg in parts[:-1]):
        return True
    name = parts[-1]
    stem = name.split(".", 1)[0]
    return (
        stem.startswith("test_")
        or stem.endswith("_test")
        or ".test." in name
        or ".spec." in name
    )


@dataclass(frozen=True, slots=True)
class RadiusNode:
    """One code node in the blast radius, tagged by how it relates to the integration point."""

    item: StructuralItem
    relation: str  # "caller" (what breaks) | "callee" (what it uses) | "container"
    distance: int  # hops from the integration point (1 = direct)
    linked_memory_count: int  # cross-links attached here — the per-node coverage signal


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """How much of the in-scope code the brain actually has recorded knowledge about.

    The honesty field: ``nodes_without_context`` is *unverified absence* — the brain may
    simply hold no memory for those nodes, not that they are known to be constraint-free.
    """

    radius_nodes: int  # nodes in scope (integration point + blast radius)
    nodes_with_context: int  # how many carry >= 1 *symbol-level* cross-link (direct)
    files_in_scope: int = 0  # distinct modules (files) the in-scope nodes live in
    files_with_context: int = 0  # how many of those files carry >= 1 cross-link

    @property
    def nodes_without_context(self) -> int:
        return max(self.radius_nodes - self.nodes_with_context, 0)


@dataclass(frozen=True, slots=True)
class PlanBrief:
    """The assembled planning brief for a target — a structured, deterministic payload."""

    target: str
    integration_point: StructuralItem | None  # None when the target did not resolve
    resolution_relevance: float | None = None
    resolution_ambiguous: bool = False  # a runner-up resolved nearly as well
    alternatives: tuple[StructuralItem, ...] = ()  # other plausible integration points
    high_fanout: bool = False  # the hub circuit-breaker tripped
    fanout_callers: int = 0  # direct caller count when high_fanout
    blast_radius: tuple[RadiusNode, ...] = ()
    constraints: tuple[MemoryItem, ...] = ()  # constraint / gotcha memories in scope
    context: tuple[MemoryItem, ...] = ()  # decision / investigation / episode memories in scope
    coverage: CoverageReport = field(default_factory=lambda: CoverageReport(0, 0))
    radius_omitted: int = 0  # radius nodes dropped past the node budget
    memories_omitted: int = 0  # memories dropped past the memory budget

    def render(self) -> str:
        """Render the brief as a clean text block for the actuator — coverage honesty included."""
        lines = [f"# Plan brief: {self.target}"]

        if self.integration_point is None:
            lines += ["", f'Could not resolve "{self.target}" to a known code node.']
            lines.append("(No blast radius — the target is not in Brain 2, or below the floor.)")
            return "\n".join(lines) + "\n"

        ip = self.integration_point
        loc = f" — {ip.location}" if ip.location else ""
        rel = f" [relevance {self.resolution_relevance:.2f}]" if self.resolution_relevance else ""
        lines += ["", "## Integration point", f"- ({ip.kind}) {ip.label}{loc}{rel}"]
        if self.resolution_ambiguous and self.alternatives:
            alts = ", ".join(f"{a.label}" for a in self.alternatives)
            lines.append(f"  ⚠ ambiguous target — also plausible: {alts}")

        lines += ["", "## Blast radius — what depends on this"]
        if self.high_fanout:
            lines.append(
                f"  ⚠ shared infrastructure — {self.fanout_callers} direct callers. The blast "
                "radius is broad and not enumerated; change behind a compatible interface or with "
                "a deprecation/migration strategy."
            )
        if self.blast_radius:
            for relation, heading in (
                ("caller", "callers (what breaks if you change it)"),
                ("subtype", "implementors / subtypes (what breaks if you change the contract)"),
                ("callee", "uses (what it calls)"),
                ("co-change", "frequently changed alongside (historical co-change)"),
                ("container", "container"),
            ):
                group = [rn for rn in self.blast_radius if rn.relation == relation]
                if not group:
                    continue
                lines.append(f"- {heading}:")
                for rn in group:
                    notes = (
                        f"{rn.linked_memory_count} note(s)"
                        if rn.linked_memory_count
                        else "no recorded context"
                    )
                    loc = f" — {rn.item.location}" if rn.item.location else ""
                    lines.append(f"  - ({rn.item.kind}) {rn.item.label}{loc}  [{notes}]")
        elif not self.high_fanout:
            lines.append("  (no direct callers/callees recorded in Brain 2)")
        if self.radius_omitted:
            lines.append(f"  - ... {self.radius_omitted} further node(s) omitted past the budget")

        self._render_memories(lines, "Known constraints & gotchas", self.constraints)
        self._render_memories(lines, "Decisions & context", self.context)

        cov = self.coverage
        lines += ["", "## Coverage"]
        if cov.radius_nodes:
            lines.append(
                f"{cov.nodes_with_context} of {cov.radius_nodes} in-scope node(s) have "
                f"symbol-level context; {cov.nodes_without_context} have NONE — absence there is "
                "*unverified* (the brain may simply hold no memory for that code)."
            )
        if cov.files_in_scope:
            lines.append(
                f"File-level: {cov.files_with_context} of {cov.files_in_scope} file(s) in scope "
                "carry recorded notes — cross-links are file-granular, so these decisions/gotchas "
                "are about the file, not pinned to the exact symbol."
            )
        if self.memories_omitted:
            lines.append(f"({self.memories_omitted} further memory(ies) omitted past the budget.)")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_memories(lines: list[str], heading: str, items: Sequence[MemoryItem]) -> None:
        if not items:
            return
        lines += ["", f"## {heading} ({len(items)})"]
        for item in items:
            superseded = " [superseded]" if item.superseded else ""
            lines.append(f"- ({item.kind}){superseded} {item.content}")
            if item.why:
                lines.append(f"  why: {item.why}")
            if item.superseded is not None:
                note = item.superseded
                lines.append(f"  ⊘ superseded by {note.superseded_by} on {note.at}: {note.reason}")
            if item.stale_references:
                gone = ", ".join(item.stale_references)
                lines.append(f"  ⚠ may be stale — references no longer in the codebase: {gone}")


@dataclass(frozen=True, slots=True)
class PlannerConfig:
    """Tunable bounds for the planner — all swappable, none load-bearing for correctness."""

    max_resolution_hits: int = 8  # k passed to structural retrieval for target resolution
    min_relevance: float = 0.0  # floor for a resolution hit (encoder-agnostic noise floor)
    ambiguity_ratio: float = 0.95  # runner-up >= ratio * top score ⇒ flag ambiguous
    hops: int = 2  # blast-radius depth bound for caller reachability
    fanout_threshold: int = 25  # direct-caller degree above which the hub breaker trips
    node_budget: int = 40  # max blast-radius nodes in the brief
    memory_budget: int = 30  # max memories gathered into the brief
    max_memory_chars: int = 1000  # per-memory content/why truncation
    cochange_min_support: float = 2.0  # min coupling score (count symbol-level; lift file-level)
    cochange_max_nodes: int = 15  # max co-change nodes added to the radius
    cochange_max_per_file: int = 3  # cap symbols taken from any one co-changed file (anti-flood)
    cochange_skip_tests: bool = True  # drop test-mirror coupling (a symbol's own test is noise)


class Planner:
    """Assembles a :class:`PlanBrief` for a target by resolving it in Brain 2, computing a
    bounded blast radius over the structural graph, and gathering the cross-linked *why*.

    A composed peer of :class:`~thalamus.gateway.gateway.Gateway` (not a method on it): recall
    is cue→payload along the retriever chain; planning is target→node→radius→brief, a different
    operation over the same Brain-2 collaborators. Read-only against the brain (it logs no
    Tier-1 usage), so it is safe to expose even in investigate/read-only mode.
    """

    def __init__(
        self,
        *,
        graph: StructuralGraph,
        links: CrossLinkIndex,
        store: Store,
        structural_retrievers: Sequence[StructuralRetriever],
        views: DerivedViewsRef,
        cochange: CoChangeIndex | None = None,
        config: PlannerConfig | None = None,
    ) -> None:
        self._graph = graph
        self._links = links
        self._store = store
        self._structural_retrievers = tuple(structural_retrievers)
        self._views = views
        # Optional logical-coupling layer (§14, removable): when present, symbols that historically
        # change together are folded into the blast radius alongside call-graph reachability.
        self._cochange = cochange
        self._config = config if config is not None else PlannerConfig()

    def plan(self, *, target: str, scope: Scope, hops: int | None = None) -> PlanBrief:
        cfg = self._config
        depth = cfg.hops if hops is None else max(hops, 1)
        cue = Cue(text=target, scope=scope)

        # 1 — Resolve the target to a Brain-2 node (code-preferring, exact-identifier-aware).
        res = self._resolve(cue)
        if res is None:
            return PlanBrief(target=target, integration_point=None)

        # 2 — Blast radius from the resolved node (deterministic graph traversal, edge-typed).
        ref = res.ref
        radius, callers, high_fanout, radius_omitted = self._compute_radius(ref, depth)

        # 3 — Gather the why: cross-linked memories for the integration point + every radius node.
        scope_refs = [ref, *(rn.item_ref for rn in radius)]
        constraints, context, coverage, mem_omitted = self._gather(scope_refs)

        return PlanBrief(
            target=target,
            integration_point=res.item,
            resolution_relevance=res.relevance,
            resolution_ambiguous=res.ambiguous,
            alternatives=res.alternatives,
            high_fanout=high_fanout,
            fanout_callers=len(callers),
            blast_radius=tuple(rn.node for rn in radius),
            constraints=constraints,
            context=context,
            coverage=coverage,
            radius_omitted=radius_omitted,
            memories_omitted=mem_omitted,
        )

    def _resolve(self, cue: Cue) -> _Resolution | None:
        """Resolve a target to a Brain-2 node: an exact symbol name wins, else semantic (code).

        The dogfood showed two failure modes: a descriptive target resolving to a doc *section*
        (prose matches doc bodies), and one whose named symbol ("IntegrationService") was *not even
        in* the semantic top-k because surrounding prose ("registry/registered/seam") pulled other
        symbols in. So we try a full-graph exact-name lookup FIRST (a code symbol literally named by
        a token in the target), then fall back to semantic retrieval keeping code over docs."""
        lexical = self._lexical_resolve(cue)
        if lexical is not None:
            return lexical

        cfg = self._config
        hits = ranked_hits(
            cue,
            self._structural_retrievers,
            k=cfg.max_resolution_hits,
            min_relevance=cfg.min_relevance,
        )
        if not hits:
            return None
        pool = [(c, s) for c, s in hits if not c.lower().startswith("docs")] or hits
        top_corpus, top = pool[0]
        ambiguous = len(pool) > 1 and pool[1][1].score >= top.score * cfg.ambiguity_ratio
        alternatives = (
            tuple(StructuralItem.from_scored_node(s, corpus=c) for c, s in pool[1:4])
            if ambiguous
            else ()
        )
        return _Resolution(
            item=StructuralItem.from_scored_node(top, corpus=top_corpus),
            ref=top.node.ref,
            relevance=top.score,
            ambiguous=ambiguous,
            alternatives=alternatives,
        )

    def _lexical_resolve(self, cue: Cue) -> _Resolution | None:
        """Exact symbol-name resolution over the whole graph (not just the semantic top-k).

        For each code-identifier token in the target (first-seen order), find code symbols whose
        bare name matches exactly; the first token with matches wins, preferring a type over a
        callable and then the shortest qualified id. Returns ``None`` when the target carries no
        identifier token or none matches — the semantic path then handles it."""
        tokens = _identifier_tokens(cue.text)
        if not tokens:
            return None
        by_name: dict[str, list[StructuralNode]] = {}
        for kind in _CODE_KINDS:
            for node in self._graph.nodes_of_kind(cue.scope, kind):
                by_name.setdefault(_simple_name(node.label).lower(), []).append(node)
        for token in tokens:
            matches = by_name.get(token)
            if not matches:
                continue
            ranked = sorted(matches, key=lambda n: (_KIND_RANK.get(n.kind, 99), len(n.node_id)))
            chosen = ranked[0]
            return _Resolution(
                item=StructuralItem.from_node(chosen),
                ref=chosen.ref,
                relevance=1.0,  # exact name match — maximal confidence
                ambiguous=len(ranked) > 1,
                alternatives=tuple(StructuralItem.from_node(n) for n in ranked[1:4]),
            )
        return None

    def blast_radius_refs(
        self, ref: StructuralRef, *, hops: int | None = None
    ) -> frozenset[StructuralRef]:
        """The blast-radius node refs around a *known* node — the deterministic traversal alone.

        Bypasses target resolution and gather (those are the semantic / experiential legs); this
        is the verifiable core the impact eval measures (does a historically-coupled node fall in
        the radius?). Honours the fan-out breaker, so it reflects the tool's real behaviour: a hub
        target enumerates no callers, by design."""
        depth = self._config.hops if hops is None else max(hops, 1)
        entries, _callers, _high_fanout, _omitted = self._compute_radius(ref, depth)
        return frozenset(entry.item_ref for entry in entries)

    def is_high_fanout(self, ref: StructuralRef) -> bool:
        """Whether ``ref``'s direct-caller degree trips the hub circuit-breaker."""
        callers = self._graph.neighbors(ref, edge_types=("calls",), direction="in")
        return len(callers) > self._config.fanout_threshold

    def _compute_radius(
        self, ref: StructuralRef, depth: int
    ) -> tuple[list[_RadiusEntry], list[StructuralNode], bool, int]:
        """Direct callers + the budgeted edge-typed frontier + the fan-out verdict — shared by
        :meth:`plan` and :meth:`blast_radius_refs` so both traverse identically."""
        callers = self._graph.neighbors(ref, edge_types=("calls",), direction="in")
        high_fanout = len(callers) > self._config.fanout_threshold
        entries, omitted = self._blast_radius(ref, depth, callers, high_fanout)
        return entries, callers, high_fanout, omitted

    def _blast_radius(
        self,
        ref: StructuralRef,
        depth: int,
        callers: Sequence[StructuralNode],
        high_fanout: bool,
    ) -> tuple[list[_RadiusEntry], int]:
        """The budgeted, edge-typed frontier around ``ref``, in priority order.

        Priority: direct callers (what breaks) → subtypes (implementors/subclasses — the "what
        breaks" for an interface/base class) → direct callees (what it uses) → container → deeper
        callers. The hub breaker suppresses caller enumeration entirely (the broad-radius flag is
        more useful than hundreds of call-sites). ``contains`` never propagates the radius (a parent
        module tells you nothing about what breaks)."""
        cfg = self._config
        callees = self._graph.neighbors(ref, edge_types=("calls",), direction="out")
        # Reverse implements/inherits: who realizes this interface / extends this class. For a type
        # definition (no callers), these implementors/subclasses ARE the blast radius.
        subtypes = self._graph.neighbors(ref, edge_types=("implements", "inherits"), direction="in")
        container = self._graph.neighbors(ref, edge_types=("contains",), direction="in")

        entries: list[_RadiusEntry] = []
        seen = {ref.node_id}

        def add(nodes: Sequence[StructuralNode], relation: str, distance: int) -> None:
            for node in nodes:
                if node.node_id in seen:
                    continue
                seen.add(node.node_id)
                item = StructuralItem.from_node(node)
                count = len(self._links.memories_for(node.ref))
                entries.append(
                    _RadiusEntry(
                        node=RadiusNode(
                            item=item,
                            relation=relation,
                            distance=distance,
                            linked_memory_count=count,
                        ),
                        item_ref=node.ref,
                    )
                )

        if not high_fanout:
            add(callers, "caller", 1)
        add(subtypes, "subtype", 1)
        add(callees, "callee", 1)
        add(container, "container", 1)
        # Co-change ranks above deeper transitive callers: the eval showed logical coupling
        # predicts impact better than 2-hop call reachability. Independent of the call breaker.
        self._add_cochange(ref, seen, entries, add)
        if not high_fanout and depth >= 2:
            deeper = self._graph.k_hop(ref, depth, edge_types=("calls",), direction="in")
            add(deeper, "caller", 2)

        if len(entries) <= cfg.node_budget:
            return entries, 0
        return entries[: cfg.node_budget], len(entries) - cfg.node_budget

    def _add_cochange(
        self,
        ref: StructuralRef,
        seen: set[str],
        entries: list[_RadiusEntry],
        add: Callable[[Sequence[StructuralNode], str, int], None],
    ) -> None:
        """Fold in symbols that historically co-changed with ``ref`` (above the coupling floor).

        A no-op without a co-change index (the removable-layer contract). Partners gone from the
        current graph are skipped (stale); scores are sorted descending, so we stop at the floor.

        Two anti-flood guards (both removable via config): file-level co-change expands one coupled
        *file* into *all* its symbols, so a single partner file could otherwise consume the whole
        budget — ``cochange_max_per_file`` caps symbols taken from any one file, spreading the
        budget across distinct coupled files; ``cochange_skip_tests`` drops a symbol's own test
        file, whose co-change is the trivial mirror, not the cross-cutting coupling we want."""
        if self._cochange is None:
            return
        cfg = self._config
        added = 0
        per_file: dict[str, int] = {}
        for partner, score in self._cochange.cochanged(ref):
            if score < cfg.cochange_min_support or added >= cfg.cochange_max_nodes:
                break
            if partner.node_id in seen:
                continue
            node = self._graph.get(partner)
            if node is None:
                continue
            path = node.anchor.path if node.anchor is not None else None
            if path is not None:
                if cfg.cochange_skip_tests and _is_test_path(path):
                    continue
                if per_file.get(path, 0) >= cfg.cochange_max_per_file:
                    continue
                per_file[path] = per_file.get(path, 0) + 1
            add([node], "co-change", 1)
            added += 1

    def _containing_module(self, ref: StructuralRef) -> StructuralRef | None:
        """Walk ``contains`` up from a symbol to the module (file) node that holds it.

        Cross-links are created at **module granularity** (``structural.linking`` links a memory to
        the module of each touched file — no finer footprint than git's per-file diff), so a
        symbol's recorded context lives on its module, not on the symbol itself. Rolling up here is
        the gather-side mirror of recall's k-hop spread (coarse link + graph spreading → fine
        context). Returns ``ref`` if it is already a module, else ``None`` if no module is found."""
        node = self._graph.get(ref)
        if node is not None and node.kind == "module":
            return ref
        current = ref
        for _ in range(_MAX_CONTAINER_DEPTH):
            parents = self._graph.neighbors(current, edge_types=("contains",), direction="in")
            if not parents:
                return None
            module = next((p for p in parents if p.kind == "module"), None)
            if module is not None:
                return module.ref
            current = parents[0].ref
        return None

    def _gather(
        self, scope_refs: Sequence[StructuralRef]
    ) -> tuple[tuple[MemoryItem, ...], tuple[MemoryItem, ...], CoverageReport, int]:
        """Collect cross-linked memories for the in-scope nodes, deduped, partitioned, budgeted.

        Returns ``(constraints, context, coverage, memories_omitted)``. Links are module-granular
        (see :meth:`_containing_module`), so we harvest from each in-scope symbol's **direct** links
        *and* its **containing module's** links — without the rollup the gather is blind to
        file-scoped decisions/gotchas even when the brain holds them. Coverage is reported at both
        granularities: ``nodes_with_context`` counts symbols with a direct (symbol-level) link
        (sparse until finer linking lands), and ``files_with_context`` counts in-scope files
        (modules) carrying notes — the granularity at which the brain actually records today. A file
        counts toward coverage independent of the memory budget — coverage reflects what the brain
        *holds*, not what fit in the brief."""
        cfg = self._config
        views = self._views.views
        seen_mem: set[MemoryId] = set()
        constraints: list[MemoryItem] = []
        context: list[MemoryItem] = []
        with_context = 0
        omitted = 0

        # Distinct containing modules of the in-scope symbols (links live here), preserving order.
        modules: dict[str, StructuralRef] = {}
        for ref in scope_refs:
            if self._links.memories_for(ref):
                with_context += 1  # a direct, symbol-level link
            module = self._containing_module(ref)
            if module is not None:
                modules.setdefault(module.node_id, module)
        files_with_context = sum(1 for m in modules.values() if self._links.memories_for(m))

        # Harvest: direct symbol links first (most precise), then the module-rollup links.
        for ref in (*scope_refs, *modules.values()):
            for memory_ref in self._links.memories_for(ref):
                if memory_ref.memory_id in seen_mem:
                    continue
                seen_mem.add(memory_ref.memory_id)
                record = self._store.get(memory_ref)
                if record is None:
                    continue
                if len(constraints) + len(context) >= cfg.memory_budget:
                    omitted += 1
                    continue
                item = MemoryItem.from_scored(
                    ScoredMemory(record=record, score=0.0),
                    max_content_chars=cfg.max_memory_chars,
                    stale_references=views.stale_references.get(memory_ref, ()),
                    superseded=views.superseded.get(memory_ref),
                )
                (constraints if record.kind in _CONSTRAINT_KINDS else context).append(item)

        coverage = CoverageReport(
            radius_nodes=len(scope_refs),
            nodes_with_context=with_context,
            files_in_scope=len(modules),
            files_with_context=files_with_context,
        )
        return tuple(constraints), tuple(context), coverage, omitted


@dataclass(frozen=True, slots=True)
class _RadiusEntry:
    """Internal: a built :class:`RadiusNode` paired with its ref (for the gather step)."""

    node: RadiusNode
    item_ref: StructuralRef


@dataclass(frozen=True, slots=True)
class _Resolution:
    """Internal: the outcome of resolving a target to a Brain-2 node."""

    item: StructuralItem
    ref: StructuralRef
    relevance: float
    ambiguous: bool
    alternatives: tuple[StructuralItem, ...]
