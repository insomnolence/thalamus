# Project Thalamus

A persistent **brain** for agentic LLMs — long-term memory, recall, and understanding
that survives across sessions, updates itself, and gets more useful the more it is used.

## Why

LLMs are predictive and stateless. They have no real memory or understanding. The current
state of practice — frameworks and files used as "memory" — forgets, never updates stored
knowledge, and is often ignored or useless. Thalamus models the thing that actually works
for this: a brain with experiences and consolidated knowledge that an agent can lean on.

## What it is *not*

- It does **not** try to replace the LLM (that was an earlier project of ours' failed bet).
- It does **not** put a JEPA / world model in the execution path. JEPA was explicitly dropped
  for this project — see the design notes for the full reasoning.
- It does **not** make an exotic model (JEPA, LNN, SNN, Hopfield, …) the *foundation*.
  Novelty lives in a removable, measurable layer *on top of* a working base.

## Status

The step-0/1 baseline is implemented and actively dogfooding. Brain 1 (experiential)
and Brain 2 (structural) are both live in a long-running MCP serve:

- Scoped experiential storage, BGE retrieval, MCP `recall` / `remember` / `record_usage` / `plan`
- Raw trajectory / retrieval / usage logs; explicit retained memories; evaluation harness
- **Hybrid retrieval:** semantic (vector) fused with a BM25 lexical leg so exact identifiers and
  rare terms surface; plus retrieval rungs ablated by a utility-join eval (centrality wins, usage ON
  with tradeoff, structural-relevance off by default) and belief supersession-demotion, each ablatable
  behind the retriever seam
- **The `plan` impact tool:** resolve a target → an edge-typed, budget-bounded blast radius
  (callers / subtypes / callees / git co-change / container, with a hub circuit-breaker) → the
  cross-linked decisions/gotchas → one coverage-honest brief; validated by a git-derived eval
- **Warm capture phase:** the serve perceives new git commits into Brain 1 on a periodic
  background clock, reusing the warm encoder — no per-commit cold start, no external hook
- **Brain 2 auto-refresh:** a `StructuralRederivePass` re-derives the code/doc graph from
  current source on the same background clock — hash-gated (a no-change tick is ~free),
  re-embeds only changed files, live, no restart needed
- **Declarative `[[corpus]]` config:** Brain 2 corpora are declared per project in
  `thalamus.toml` as `[[corpus]]` tables (`kind = python-ast | scip | docs | text | findings`,
  `root`, `include` globs, optional `regen_command`); language-agnostic — any language with a
  SCIP indexer works with no new ingestor code
- **The learning loop (more use → more useful):** offline `dreaming` passes on the background
  clock consolidate the brain's *own behavioral history into the brain itself* — which memories
  were recalled-and-used, across which sessions — so a **usage-weighted recall rung** (fused with
  **structural-centrality**) lifts the reliably-useful core, read from the brain, not a file scan.
  Footprint usage-attribution and the cross-link graph are likewise refreshed in-loop. Every
  learning signal is a behavioral *act* (used / superseded / recent / co-changed / graph-central),
  never the model grading its own prose (the §14 firewall)
- **Honest measurement, by design:** the append-only retrieval / usage / trajectory logs feed a
  proxy↔truth `verdict` (does surfaced context map to real committed work?), a utility-join rung
  ablation, and calibrated exploration with a logged propensity (off by default) so off-policy
  evaluation has common support later. Instruments first, so the baseline is measured from day one

Design notes and architecture live in [`docs/design-notes.md`](docs/design-notes.md).

## Core shape

- **Two hemispheres**, kept separate (a single polluted store caused real problems before):
  - **Brain 1 — Experiential / autobiographical:** what we did and *why*, preferences,
    history. Irreplaceable, append-mostly, consolidated over time.
  - **Brain 2 — Structural / reference knowledge:** a re-derivable corpus graph — code
    (AST + call graph), docs, arbitrary re-ingested material — declared per project via
    `[[corpus]]` entries in `thalamus.toml`. Language-agnostic; auto-refreshed live.
- **A single gateway conduit** (MCP / FastMCP) is the only interface the LLM agent touches.
  Recall fuses both hemispheres: experiential memory plus the structural context it touched.
- **The LLM is an actuator** working in a tight, constraint-rich window fed by the brain —
  not an orchestrator. The brain gives it the whole-system picture so its local edits are
  globally informed.
- **One graph, two namespaced vector indexes** — separate vector spaces per hemisphere (no
  retrieval pollution) with native cross-hemisphere edges linking "why we did X" to "where
  X lives in the code."
- **It consolidates and learns from itself.** Like sleep, an offline *dreaming* phase runs on a
  background clock: it re-derives Brain 2 from current code, re-links experiential memories to the
  code they touched, demotes superseded beliefs, and folds the brain's own usage history into
  durable state — so recall gets better over time without a restart. The raw logs are a disposable
  write-ahead buffer; the brain is the system of record. Every adaptation is gated, removable, and
  measured against a boring baseline (the §14 discipline), never a self-referential signal.

See the design notes for the architecture, research map, open questions, and roadmap.

## Where we're going

The near-term roadmap, in build order (most now shipped):

1. ✓ **Hybrid retrieval** — combine semantic (vector) and BM25 lexical scoring so exact
   identifiers, error strings, and rare terms are reliably surfaced (RRF-fused behind the
   retriever seam, ablatable against the eval harness). **Shipped.**
2. ✓ **Universal reference-knowledge ingestion** — the corpus/Ingestor model is a pluggable
   producer registry: any source that emits structured nodes/edges or text chunks becomes a
   Brain-2 corpus. Built-in producers today: `python-ast`, `scip`, `docs`, `text`, `findings`.
   Adding a kind is a registration, not a build-dispatch edit. Guardrail: intentional curation,
   not "ingest everything." **Shipped.**
3. **Multi-language structural ingestion** — *partly shipped:* any language with a SCIP indexer
   works today via the `scip` producer (TS / Rust / Go / C++ / …); a lightweight tree-sitter
   ingestor for languages without a SCIP indexer is still future.
4. ✓ **External analysis producers** — a `findings` producer ingests SARIF / generic-JSON
   analysis results (linters, security scanners, deeper analyzers) as a derived,
   regenerated-on-change Brain-2 corpus. Thalamus aggregates their output; it never runs the
   analysis engine in the recall path. **Shipped** (retrievable corpus today; surfacing findings
   inside the `plan` blast radius via finding→code edges is the next step).
5. ✓ **`plan` / impact tool** — given a target, resolve the integration point (exact symbol-name
   lookup, then semantic code-preferring fallback), compute the edge-typed blast radius (callers /
   subtypes / callees / co-change logical coupling / container, budget-bounded with a hub
   circuit-breaker), gather the decisions/constraints/gotchas cross-linked to everything in scope,
   and return one fused brief with a coverage honesty report. Validated via a git-derived
   `impact-eval` CLI. **Shipped.**
6. **`research` tool** — a deeper cross-hemisphere "what do we know about X" synthesis.

**Honest framing:** having the architecture does not guarantee the briefs are globally
informed rather than stapled-together lists. Synthesis quality is the ongoing frontier,
gated on capture discipline, retrieval quality, and the dogfood/verdict loop that measures
whether the brain makes the LLM more accurate and efficient. The vision is buildable and
measurable. See [`docs/design-notes.md`](docs/design-notes.md) §16 for the full roadmap.

## Dogfood Workflow

Run a durable local Brain 1 with Neo4j:

```bash
docker volume create thalamus-neo4j-data
docker run -d --name thalamus-neo4j --restart unless-stopped \
  -v thalamus-neo4j-data:/data \
  -e NEO4J_AUTH=neo4j/thalamuspw -p 7687:7687 neo4j:5
export THALAMUS_NEO4J_URI=bolt://localhost:7687
export THALAMUS_NEO4J_USER=neo4j
export THALAMUS_NEO4J_PASSWORD=thalamuspw
```

Commands default to real BGE embeddings. When invoking from the workspace, enable the
routing package's optional encoder dependency:

```bash
uv run --package thalamus-routing --extra bge python -m thalamus.cli remember \
  --repo . --kind decision \
  --text "Memory and structural identities are scoped by tenant and repository." \
  --why "Unscoped ids can overwrite another repository's facts." \
  --file packages/core/src/thalamus/core/types.py

uv run --package thalamus-routing --extra bge python -m thalamus.cli sync --repo .

uv run --package thalamus-routing --extra bge python -m thalamus.cli serve --repo . \
  --max-structural-items 12 --max-memory-chars 1000
```

`remember` stores high-value explicit facts (`decision`, `constraint`, `gotcha`,
`investigation`, or `preference`) and can attach file footprints for structural context.
`sync` materializes derived commit episodes. `serve` exposes bounded MCP recall and logs
retrieval and usage events beneath `.thalamus/logs/`.

Claude Code can use the project-scoped [`.mcp.json`](.mcp.json) server configuration once
Neo4j is running. On first use, approve the project MCP server when Claude prompts; verify
it with `claude mcp get thalamus`. The MCP server exposes `recall`, `record_usage`, and
`remember`; ask Claude to retain durable decisions or gotchas during a task, for example:

```text
Use the Thalamus remember tool to store this constraint: changing the vector encoder
requires rebuilding compatible indexes. This applies to packages/store/src/thalamus/store/neo4j_store.py.
```

Memories written through MCP are semantically recallable immediately. Optional related-file
structural links are resolved on the next background maintenance tick (the same clock that
auto-refreshes Brain 2); a `remember` also triggers an immediate consolidation cycle.

To attach a terminal test result to a real task session:

```bash
uv run python -m thalamus.cli capture-tests --repo . --junit report.xml \
  --session-id <session-id> --terminal
```
