# Deep dive — Foundation (the boring base; step-0/1 build spec)

*Part of [Project Thalamus design notes](../design-notes.md), worked through 2026-05-24. Mostly
*reuse + decisions*, not novel design. `§13.x` / **OLR §13.x** → [`outcome-learned-retrieval.md`](outcome-learned-retrieval.md);
§1–§15 → [`../design-notes.md`](../design-notes.md).*

This pass resolves the open architectural decisions and pins what step 0/1 concretely is — the point
at which **building beats further designing** (nothing past step 1 is *evaluable* without step 1's logs).

---

## Decision 1 — one store vs. two (resolves §4, §12; audit #7)

The §4 default was two *physically separate* stores (a polluted store hurt before). But the design then
leans hard on cross-hemisphere linking (§13.19), footprint-joins (§13.16), and Brain 2 auditing Brain 1
(§13.18-D2) — separating then constantly bridging. What actually broke before was **retrieval/index
pollution** (a shared *vector index* mixing experiential and structural neighbours), **not shared
storage.** That precision gives the resolution:

> **One graph substrate, two logical namespaces (typed nodes), *separate vector indexes per hemisphere*,
> cross-hemisphere links as native edges.**

- **Keeps what mattered:** separate indexes → no retrieval pollution; separate lifecycle → wipe-and-rebuild
  the re-derivable Brain 2 nodes without touching the irreplaceable Brain 1 nodes; separate backup policy.
- **Gains:** §13.19 linking is native edges (not cross-store joins); cross-hemisphere k-hop spreading is
  one traversal; symbol-identity re-resolution is local.
- **Fits the reusable asset:** Neo4j gives typed nodes (labels), native edges, and per-label vector
  indexes out of the box (§11).

## Decision 2 — single-operator by design (and what the scope tuple actually buys)

**The brain is single-operator — permanently and deliberately, not "single-user for now."** This is
the canonical statement; other docs point here rather than re-litigating it. The reasoning is
architectural, not a matter of scale or maturity:

The experiential hemisphere is **first-person**. Episodes are *this* agent's work; curated memories
are *its* decisions; usage signals are what *it* recalled-and-used; and the firewall (OLR §13.7)
elevates by *its own* behavioral facts. There is no coherent "shared experience" without deciding
whose judgment counts — so "multi-user" is not a deferred frontier, it's a non-question:

- **Isolate users** for the security and workflow reasons you must → you have **N independent
  single-operator brains**, i.e. N copies, not a multi-user system. The lone shareable part, Brain 2,
  is a *re-derivable* function of the corpus — sharing it is a cache, not shared knowledge.
- **Don't isolate them** (cross-user experiential sharing — "learn from your teammates' gotchas") →
  that's a **different product** (team/org memory) that breaks the firewall and the per-user trust
  model, and it isn't this thesis. Out of scope.

So there is no multi-user shape that advances *this* project; the question is closed.

What the `(tenant_id, repo_id)` scope on every node / episode / log row **does** buy is
**multi-*project*** namespacing: one operator across many repos, repo A's facts never colliding with
repo B's. That's real and used — it is *namespacing, not multi-tenancy*. No RLS, auth, or cross-tenant
pooling exists or is planned (security.md §17.3).

## The gateway — the one foundation piece that needs real care

Everything else is reuse; the gateway is the product surface (§4):

- **In:** prompt + current focus. **Out:** structured payload — target code, structural deps,
  gotchas/constraints, relevant experiential memories *with their whys*, style prefs.
- **Home of the retrieval-event log** (OLR §13.11a): it sees every query, candidate set, what it
  returned, propensities. The *trajectory* log comes from out-of-band observers (watcher / git / test
  hook), not the gateway.
- **Payload assembly is where practical quality lives** — our predecessor project's hard lesson (§5.6: good retrieval
  stuffed badly ≠ usable understanding). The graph-context renderer (§11, "weeks of tuning") transfers.
- **MCP / FastMCP** protocol → usable with off-the-shelf editors immediately.

## Reuse vs. new-build map

| Piece | Status | Source / note |
|---|---|---|
| Brain 2 (AST graph, k-hop query, renderer, symbol routing) | **reuse** | §11; new bit = symbol-identity resolution across re-parses (§13.19) |
| Routing (BGE + labeled intent classifier) | **reuse** | §11; boring |
| Store (graph + per-label vector index) | **reuse** | Neo4j (§11) |
| Gateway (MCP conduit + payload assembly) | **reuse + new** | renderer reuse; MCP surface new |
| Brain 1 (episodic + why capture, vector retrieval) | **new** | the genuinely new build; start *coarse* (see below) |
| Out-of-band observers (watcher, git hooks, **test-runner hook**) | **new** | test hook = the critical capture unlock (OLR §13.11) |

**Reuse verified (2026-05-24)** against an earlier project of ours — same toolchain choices (uv
workspace, `requires-python >=3.12`, a `src/<project>/<pkg>` src-layout, Pydantic `extra="forbid"`
config + `core` protocols/exceptions). All §11 assets were **present and usable**: the AST
code-ingestor (plus a tree-sitter ingestor and a trajectory extractor), the in-memory + Neo4j stores
and knowledge graph, the temporal / memory-decay graph, the graph-context renderer + source-excerpt
layer, the text dispatcher, and the parked experience-weighted-retrieval experiment (a small parked
branch with a design doc — the step-2 seed).

**Drifted (needs cleanup on reuse):** the encoder was tangled into application + outcome-scoring code
(the latter being the self-referential reward we avoid) → extract a clean BGE wrapper. **Avoid (lesson
only, §11):** the JEPA orchestrator/worker layer, the self-referential outcome scorer, and the
JEPA/talker/slot config. **Verdict:** "boring base is mostly reuse, low-risk" holds — build estimate de-risked.

**Reuse method — reference, not copy.** Reuse the predecessor project heavily (don't reinvent the wheel), but for
each asset: (1) read + assess *what it did well vs. its incurred tech debt* (JEPA/orchestrator coupling,
dead abstractions, self-referential bits, over-engineering); (2) **reimplement cleanly** in Thalamus,
carrying forward the good and dropping the debt — never lift-and-shift debt with the code. Record the
per-asset verdict here as we go. Likewise, don't mirror the predecessor's package layout by default — justify
Thalamus's structure on its own merits (below).

**Per-asset verdicts (filled as reused):**
- The predecessor's sentence-encoder → **reimplemented** as `routing.BgeEncoder`. *Kept:* lazy
  model load, `TYPE_CHECKING` import guard, BGE default. *Dropped:* torch in the public surface (returns
  plain float lists), single-string API (now batch), outcome-scorer coupling.
- Its in-memory store → **reimplemented** as `store.InMemoryStore`. *Kept:*
  dim validation + structured error, defensive copies, top-k by similarity. *Dropped:* torch (pure
  Python), `dict[str,str]` metadata (now `MemoryRecord` + `Scope`), unit-norm assumption (true cosine).
  *Added:* scope filtering.

**Brain 1 starts coarse and doesn't block step 1:** request-bounded episodes (OLR §13.16-S0) + why as
evidenced primitives (OLR §13.17) + a vector index + append-with-basic-supersession beliefs. All the
sophistication (relabeling, reconciliation, dreaming) is gated step 2–4 and *needs step 1's logs* to be
evaluable — so Brain 1's open schema questions refine later in dreaming, not now.

## Concrete step-0/1 spec (the build-readiness line)

**Step 0 — instrument (before any baseline):**
- Two append-only logs: retrieval-event (via gateway, OLR §13.11a) + episode-trajectory (watcher + git
  hooks + **test-runner hook**, OLR §13.11b).
- Eval harness + a small curated benchmark (OLR §13.20).
- Deterministic cross-hemisphere link substrate (native edges, symbol-identity resolution, §13.19).
- Per-project (`tenant_id`, `repo_id`) namespacing on the schema & logs (Decision 2).

**Step 1 — measured baseline:**
- One Neo4j-style graph; two namespaces (Brain 1 / Brain 2 typed nodes); separate vector indexes; native
  cross-links (Decision 1).
- Brain 2: AST ingest + k-hop query + graph-context renderer (reuse).
- Brain 1: coarse episode + why capture + vector retrieval.
- Routing: BGE + intent classifier (reuse).
- Gateway: MCP conduit, retrieve → assemble → payload; writes the retrieval-event log; accepts outcome reports.
- Retrieval = frozen-BGE top-k + recency + importance (**the L0 baseline**), measured against step 0 from day one.

Everything beyond step 1 (relabeling, bent geometry, dreaming passes, belief reconciliation) is a **gated
layer that needs step 1's logs to be evaluable** → more design now has diminishing returns; the binding
constraint becomes **evidence**.

## Associative retrieval (HippoRAG) — mostly already covered

Brain 2's k-hop query + cross-hemisphere associative spreading (§13.19) + the recall-ceiling analysis
(§13.2) *is* the HippoRAG idea applied. Only open refinement: **Personalized PageRank vs. plain BFS** for
*ranking* the spread — a step-2 tuning decision over logged data, not a step-0/1 blocker.

## Honest residual risks (not blockers)

- The **test-runner hook** is real, language-specific engineering and the critical capture unlock (start
  Python: pytest plugin).
- **Payload assembly** quality is tuning-heavy even with reuse.
- Brain 1's episode/why **schema starts coarse** and is deferred to dreaming — fine *iff* raw capture is
  faithful (principle 1).
