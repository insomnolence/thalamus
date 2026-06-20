# Deep dive — Structural hemisphere (Brain 2)

*Part of [Project Thalamus design notes](../design-notes.md). Started 2026-05-25. `§13.x` →
[`outcome-learned-retrieval.md`](outcome-learned-retrieval.md); §1–§16 → [`../design-notes.md`](../design-notes.md).*

## Reframe: Brain 2 is a *re-derivable corpus*, not "code"

The hemispheres split by **kind**, not content: Brain 1 = *irreplaceable* (what we did/why); Brain 2 =
*re-derivable* (re-parse the source to rebuild it). "Code AST graph" is just the canonical re-derivable
corpus. A **document corpus you can re-ingest is the same kind** and belongs here too. So Brain 2 is a
re-derivable structural graph over an external corpus — code first, others behind the same seam.

## The seam + schema (corpus-agnostic)

- **`Ingestor` protocol** (`ingest_path(root) -> IngestResult`): the swappable seam. Language/format-specific
  implementations produce typed nodes+edges into one shared graph.
- **Schema** (open typed vocabulary, *not* code-specific): `StructuralNode{node_id, kind, label, anchor,
  metadata}`, `StructuralEdge{source_id, target_id, type}`, `SourceAnchor{path, line_start, line_end}`.
  `node_id` is a **stable identity** surviving re-ingestion (e.g. `function:pkg.mod.func`).
- **Per-corpus index/namespace** (the no-pollution principle, same as separate per-hemisphere vector
  indexes): a docs corpus and a code corpus don't muddy each other's retrieval — effectively a third
  scoping axis alongside tenant/repo.

## Built

- **`PythonAstIngestor`** (stdlib `ast`, deterministic, zero-dep): modules / classes / functions /
  methods with stable qualified ids + source anchors; `contains` / `inherits` / `imports` edges.
  Same-module inheritance resolved; raw base names kept in node metadata.
- **`JediCallIngestor`**: delegates call resolution to jedi (a proven resolver, not a hand-rolled
  type-checker — §4/§5), emitting `calls` edges between canonical node ids. Composed with the AST
  pass via `CompositeIngestor`; degrades gracefully if jedi is absent.
- **`ScipIngestor`**: language-agnostic SCIP protobuf consumer — nodes (module/class/function/…),
  `calls` edges (from `enclosing_range` containment), `implements`/`inherits`/`contains`. Any
  language with a SCIP indexer (TypeScript, Rust, C++, Go, …) works with no new ingestor code.
- **`DocIngestor`**: parses Markdown into document/section nodes (heading hierarchy → `contains`
  edges; section content embedded via `node_text`). Re-derivable corpus, its own index.
- **`TextIngestor`**: the headingless sibling of `DocIngestor` for arbitrary plain text (notes,
  logs, specs, transcripts) — one `document` node + N `chunk` nodes per file (`contains` edges),
  each chunk's body embedded via `node_text`. Chunking (`chunk_lines`) is line-anchored and
  content-aware: prefers blank-line breaks, force-breaks at a char budget, carries a small
  overlap, always advances ≥1 line. `options.chunk_chars`/`overlap_chars` tune it.
- **`InMemoryStructuralGraph`**: `get` / `neighbors` / **`k_hop` BFS** (deterministic associative
  spreading — the HippoRAG-style "find connected code", §13.19, done by the graph not a model).
  Tolerates dangling edge targets (e.g. imports of external modules). Also: `Neo4jStructuralGraph`
  (persisted; native Cypher k-hop) and `Neo4jStructuralIndex` (per-corpus persisted vector index).
- **Direct structural retrieval**: `StructuralIndex` protocol + `StructuralRetriever` — Brain 2 is
  directly queryable (a cue can hit code or docs without an episode cross-link). Per-corpus indexes
  keep code and doc retrieval in separate vector spaces (the no-pollution principle).
- **`incremental_ingest`**: content-hashes corpus files before parsing; re-embeds only changed files'
  nodes; drops vanished nodes; no-change tick skips all parsing. `FileManifest` records per-corpus
  hashes in Neo4j so restarts rebuild incrementally (the §14.1 re-derive oracle).
- **Cross-hemisphere links** (`InMemoryCrossLinkIndex` / `Neo4jCrossLinkIndex`) + **gateway
  integration**: an experiential memory anchors to a structural node; `Gateway.recall` surfaces the
  linked code (+ k-hop neighbours) as a "Related code" payload section. Native
  `(M_experiential)-[:TOUCHES]->(SNode)` edge in the shared Neo4j graph.
- **`StructuralRederivePass`** (dreaming actor): re-runs `incremental_ingest` over the live graph +
  per-corpus indexes each maintenance tick — hash-gated (no-change tick is O(hash files), ~free),
  re-embeds only changed files, fully live (no restart needed). Runs before `StructuralRefreshPass`
  so freshly-added modules exist before episode footprints are re-linked. `regen` hook (optional)
  rebuilds an external index artifact (e.g. a `.scip`) before re-ingesting it.
- **Declarative `[[corpus]]` config** (`thalamus.toml`): each project declares its Brain-2 corpora
  as `[[corpus]]` tables with `name`, `root`, `kind` (`python-ast | scip | docs | text`), optional
  `scip_index`, `include` globs (for change detection and `regen_command` gating), optional
  `regen_command`, optional `root_package`, optional `options` map (forward-compatible per-producer
  params, e.g. `chunk_chars` for `text`). `build_corpora_from_configs` builds the exact `CorpusSpec`
  + index handles that both the startup build and the live re-derive pass share — so re-ingest goes
  into exactly what recall queries. A `[[corpus]]` set replaces the flat `language`/`scip_index`/
  `doc_roots` config when present; the flat path still works.
- **Producer registry** (`producer_registry.py` + `producers.py`): each `kind` maps to a `Producer`
  that owns turning a `CorpusConfig` into a `(ingestor, change-enumerator)` pair and validating it.
  `CORPUS_KINDS` derives from the registry (single source of truth), and `parse_corpora` /
  `build_corpora_from_configs` dispatch through it instead of a hardcoded `if kind == …` chain — so
  adding a corpus kind (external findings, a tree-sitter language, …) is one `register_producer(...)`
  call, not an edit to config parsing or the build. The registry is a leaf module (no CLI-composition
  imports) so the import graph stays cycle-free; entry-point / external-plugin auto-discovery is the
  deferred v2 (the in-process registry is the v1 seam).
- Tests cover ingestion (file + directory + syntax-error skip), traversal, cross-links,
  two-hemisphere gateway payload, incremental rebuild property (incremental == full-rebuild),
  SCIP fixture round-trip; all green.

## Reuse-as-reference verdict (predecessor project)

- `PythonCodeIngestor` → **reimplemented** into the corpus-agnostic schema. *Kept:* stable qualified ids,
  line-range anchors, defines/inherits/imports. *Dropped:* the GNN `KnowledgeNode`/`KnowledgeUpdate`
  types. *Fixed:* a latent `_dotted` bug (joined the full filesystem path when no `root_package`).
- `TreeSitterIngestor` → reference for the future multi-language ingestor.
- `Neo4jKnowledgeGraph.query_subgraph` → reference for the future Neo4j-backed graph.

## Built since the initial writing (updating the deferred list)

- **Universal producer pipeline** — realized. `Ingestor` is driven by a pluggable *producer
  registry*: `python-ast`, `scip`, `docs`, `text`, and `findings` are all registered built-in
  producers. Adding a corpus kind is a `register_producer(...)` call. Guardrail: intentional
  curation, not "ingest everything." (`packages/cli/src/thalamus/cli/producers.py`)
- **External analysis producers** — shipped. `FindingsIngestor` + `FindingsProducer` (kind
  `findings`) ingest SARIF or generic-JSON analysis results as a derived, regenerated-on-change
  Brain-2 corpus. Thalamus aggregates; it never runs the analysis engine in the recall path.
  Findings surface as "## Related findings" in recall. Surfacing them inside the `plan` blast
  radius via `annotates` edges is the next step (see `anchor_linking.py`, which creates those
  edges, and the planner's `_gather` step, which reads only `TOUCHES` cross-hemisphere links today).
- **Symbol-level linking infrastructure** — shipped. `SymbolResolver` (`symbol_resolution.py`)
  resolves `(file, line)` to the smallest enclosing symbol. `link_by_footprint` (`linking.py`)
  accepts `(file, touched_lines)` pairs for symbol-level linking. `link_anchored_nodes`
  (`anchor_linking.py`) creates `annotates` edges from non-code nodes to the code they annotate.
  Honest limit: live footprints from git `diff-tree` carry paths only (no line data), so
  episode linking stays module-level until line-aware footprint capture lands (ROADMAP C-8).

## Deferred / still open

- **Multi-language syntactic ingestor** — a lightweight tree-sitter (or equivalent) ingestor as a
  producer for languages whose SCIP indexer is unavailable or too heavy. Language is a plugin axis.
- **Cross-module inherit/import resolution** (v0 is same-module heuristic for Python AST) — SCIP covers
  this for supported languages; the AST path remains a heuristic.
- **Symbol-identity re-resolution across re-parse** — turn a stale cross-link into the §13.18-D2
  signal precisely (today a missing node is skipped; symbol-level staleness is not yet surfaced).
- **Single-transaction Brain-2 swap** — the live re-derive removes-then-re-MERGEs changed nodes;
  a concurrent recall in that sub-second window could see a partial state. A single-transaction
  swap (documented robustness follow-up) eliminates this.
- **Per-corpus index persistence for docs** — today the doc vector index is rebuilt at startup;
  Neo4j persistence (already done for code) would make doc warm-starts equally fast at scale.
- **Nested defs** — v0 covers top-level classes/functions + direct methods; deeply nested defs
  are not indexed.
- **`scip-python` upgrade** — the Python corpus could be re-ingested via SCIP (Pyright-based,
  precise calls) for cross-module accuracy; AST + jedi is the current pragmatic default.

## §13.19 cross-link join — live in recall (three consumers, 2026-06-15+)

The §13.19 deterministic footprint cross-link (experiential memory ↔ structural node) now feeds
three consumers beyond the "Related code" payload section:

**Query-local rung** — `StructuralRelevanceRetriever` (policy tag `+structrel`): on each recall it
derives the anchor code nodes from the cue's *own* structural retrieval, then RRF-boosts experiential
memories cross-linked to those nodes — surfacing "the *why* behind the code your query is about."
Bounded to the relevance pool (boosts existing candidates; never injects irrelevant memories),
in-chain (logged), firewall-clean (reads the cross-link graph, never model prose).

**Global rung** — `StructuralCentralityRetriever` (L-R2 global): boosts memories by their
*structural-centrality weight* — `memory_centrality` in `anchoring.py` computes Σ `(1 + degree(node))`
over the memory's cross-linked code nodes. A memory about a load-bearing, high-degree hub outweighs
one about an isolated leaf. Firewall-clean (pure graph topology, no model text). Weights recomputed
mid-serve by `CentralityRefreshPass` as Brain 2 and the cross-links evolve.

**Plan tool** — the `plan` tool's `_gather` step pulls cross-linked memories for every node in the
computed blast radius directly into the brief (see [`planning.md`](planning.md)) — not an extension
of either retrieval rung but the same underlying cross-link join used in a different context.

Limitation on the query-local rung: not probe-eval-measurable in isolation (probe-eval omits Brain
2); evaluation moves to plan-tool territory (the git-derived `impact-eval`).

## Reframe: Brain 2 is growing into general reference knowledge

The initial framing of Brain 2 as "the code graph" is narrowing. The correct frame — re-derivable
structural graph over an external corpus — applies equally to docs, externally-produced findings,
and arbitrary reference material. The `Ingestor` seam already supports this; the universal-producer
direction (above) makes it explicit policy. This is the architectural prerequisite for the planning
tool (§16, [`deep-dives/planning.md`](planning.md)): a plan query needs not just code structure but
analysis findings, documented constraints, and any other reference knowledge attached to the target.
