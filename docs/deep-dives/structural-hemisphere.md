# Deep dive — Structural hemisphere (Brain 2)

*Part of [Project Thalamus design notes](../design-notes.md). Started 2026-05-25. `§13.x` →
[`outcome-learned-retrieval.md`](outcome-learned-retrieval.md); §1–§15 → [`../design-notes.md`](../design-notes.md).*

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

## Built (v0)

- **`PythonAstIngestor`** (stdlib `ast`, deterministic, zero-dep): modules / classes / functions /
  methods with stable qualified ids + source anchors; `contains` / `inherits` / `imports` edges.
  Same-module inheritance resolved; raw base names kept in node metadata.
- **`InMemoryStructuralGraph`**: `get` / `neighbors` / **`k_hop` BFS** (deterministic associative
  spreading — the HippoRAG-style "find connected code", §13.19, done by the graph not a model).
  Tolerates dangling edge targets (e.g. imports of external modules).
- **Cross-hemisphere links** (`InMemoryCrossLinkIndex`) + **gateway integration**: an experiential
  memory anchors to a structural node; `Gateway.recall` surfaces the linked code (+ k-hop neighbours)
  as a "Related code" payload section. A link whose node is absent from the graph = stale (the
  §13.18-D2 seed; skipped for now). Demonstrated live: experiential *why* + the code it's about.
- Tests cover ingestion (file + directory + syntax-error skip), traversal, cross-links, and the
  two-hemisphere gateway payload; all green.

## Reuse-as-reference verdict (Polynoica)

- `PythonCodeIngestor` → **reimplemented** into the corpus-agnostic schema. *Kept:* stable qualified ids,
  line-range anchors, defines/inherits/imports. *Dropped:* the GNN `KnowledgeNode`/`KnowledgeUpdate`
  types. *Fixed:* a latent `_dotted` bug (joined the full filesystem path when no `root_package`).
- `TreeSitterIngestor` → reference for the future multi-language ingestor.
- `Neo4jKnowledgeGraph.query_subgraph` → reference for the future Neo4j-backed graph.

## Planned / deferred (so we don't forget)

- **Resolved `calls`/`references` edges** — the big missing capability (powers "what breaks if I change
  this signature" + richer k-hop). **Delegated, not hand-rolled**: precise call resolution needs type
  inference = reinventing a type-checker (violates §4/§5). Path: **jedi** (Python, in-process, light) →
  **SCIP / scip-python** (Pyright-based, precise + multi-language) behind the same `Ingestor` seam.
- **Cross-module inherit/import resolution** (v0 is same-module heuristic) — arrives with jedi/SCIP.
- **Multi-language** — tree-sitter ingestor (syntax) and/or SCIP indexers (precise), per language, behind
  the seam. Language is a plugin axis, not a rewrite.
- **Document ingestor** — GraphRAG/RAPTOR-style sections/entities/summaries (re-derivable corpus, fuzzier
  precision, its own index). A new ingestor, *not* a new brain.
- **Neo4j-backed `StructuralGraph`** — persist nodes+edges + native k-hop (`query_subgraph`); `InMemory`
  is v0.
- **Direct structural retrieval** — embed structural nodes (per-corpus index) so a cue can hit Brain 2
  *directly* (today structural context is reached only via cross-hemisphere links).
- **Auto-linking + staleness** — deterministic cross-hemisphere linking from an episode's trajectory
  footprint (today links are explicit), and **symbol-identity re-resolution across re-parse** to turn a
  stale link into the §13.18-D2 signal (today a missing node is silently skipped).
- **Nested defs** — v0 covers top-level classes/functions + direct methods.
