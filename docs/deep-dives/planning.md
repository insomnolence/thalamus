# Deep dive — Planning / impact fusion

*Part of [Project Thalamus design notes](../design-notes.md). §13.x →
[`outcome-learned-retrieval.md`](outcome-learned-retrieval.md); §1–§16 →
[`../design-notes.md`](../design-notes.md).*

---

## The problem: narrow actuators miss the forest

LLM agents edit locally. A change to a shared interface, a core type, or a widely-called utility
ripples through a codebase in ways the actuator cannot see unless it is explicitly shown them. The
result: the actuator fixes the tree it is touching and breaks the forest around it. Thalamus's
structural hemisphere exists precisely to make those implications visible — but recall is passive
(the agent must query it) and does not synthesize the implications into a brief.

The `plan` tool is the active counterpart: **given a target (a function, a file, a concept),
resolve where it lives, compute what depends on it, gather what the brain knows about everything it
touches, and return one fused brief** — so the actuator walks into the task already holding the
whole-system picture.

---

## Status: shipped and dogfood-validated

The tool was built, reviewed, and refined through three real dogfood rounds on a TypeScript
codebase. It is now live in the MCP gateway as a read-only `plan` tool. The pipeline, eval
harness, and co-change layer all reflect the as-built implementation; this document describes what
shipped, not the original design intent.

---

## What the tool does (as built)

`Planner.plan(target, scope, hops) -> PlanBrief` runs four steps:

### 1 — Resolve the integration point (`_resolve` / `_lexical_resolve`)

A whole-graph **exact symbol-name lookup runs first**. For each code-identifier token in the
target (CamelCase / snake_case, length ≥ 3), every code symbol whose bare name matches exactly
is found — across the whole graph, not just the semantic top-k. Among matches, types rank ahead
of callables (`interface > class > enum > function > method`); the shortest qualified id breaks
ties. This fixed two real dogfood failures: a descriptive target whose named symbol was not in the
semantic top-k (surrounding prose displaced it), and a target that resolved to a doc section
instead of the code node it named.

When no identifier token matches, the fallback is **semantic retrieval that prefers code corpora
over docs** (`pool = [(c, s) for c, s in hits if not c.lower().startswith("docs")] or hits`). A
plan targets code, not a doc section; the preference ensures that unless nothing else matches, a
doc section cannot win. Ambiguity is flagged (runner-up score ≥ 95% of top) and alternative
candidates are included in the brief.

When neither path resolves the target, the brief reports the failure explicitly:
`Could not resolve "…" to a known code node` — the honest salvage for a pure-greenfield seam.

### 2 — Compute the blast radius (`_blast_radius`, edge-typed, budget-bounded)

The blast radius is a **budgeted, edge-typed frontier**, not a hop-bounded transitive closure.
Priority order within the budget:

| Relation | Direction | What it means |
|---|---|---|
| `caller` | reverse `calls` | what breaks if you change this symbol |
| `subtype` | reverse `implements` / `inherits` | implementors / subclasses (for an interface or base class, these *are* the blast radius) |
| `callee` | forward `calls` | what this symbol uses |
| `co-change` | logical coupling from git | files that historically change together (below) |
| `container` | reverse `contains` | the containing module — included but never propagated |

Deeper callers (2-hop) are added last, after co-change, because the eval showed logical coupling
predicts impact better than 2-hop call reachability for cross-module coupling.

`contains` edges are explicitly **never propagated**: a parent module tells you nothing about what
breaks; it only identifies where the integration point lives.

**Circuit-breaker (hub nodes).** When a symbol's direct-caller count exceeds
`fanout_threshold` (default 25), the tool refuses to enumerate hundreds of call-sites and instead
emits a flag: `shared infrastructure — {N} direct callers. The blast radius is broad and not
enumerated; change behind a compatible interface or with a deprecation/migration strategy.`
This is more useful to the actuator than a list of 200 callers. The co-change layer and subtype
search still run — they are independent of the call-breaker.

The `node_budget` (default 40) caps the total radius. Nodes dropped past the budget are counted
and reported (`{N} further node(s) omitted past the budget`).

### 3 — Co-change: logical coupling from git (the differentiated value)

Co-change is a **removable layer** (`CoChangeIndex` protocol, `CoChangeRef` live-swappable
holder). When present, symbols whose *files* historically change together are folded into the
blast radius under the `co-change` relation.

**Why file-level, not symbol-level.** Symbol-level co-change drifts when mapped onto a HEAD
index: old diffs land on moved/renamed symbols. File paths are stable across revisions (the
classic Zimmermann result is file-granular). `FileCoChangeIndex` accumulates file↔file
co-occurrence counts, then answers the symbol-level query by expanding a symbol → its file →
co-changed files → the symbols that live in them.

**Scored by lift, not raw count.** `lift = P(both) / (P(home) · P(partner))` divides out a
partner's base change-rate, so a file that appears in most commits (a sweep file) scores near
chance and genuine coupling scores high. A `max_file_frequency` cap (default 10% of commits)
hard-excludes sweep files outright.

**Language-agnostic by construction.** Changed files are filtered to those that carry a code
symbol in the current graph (the `file_refs` keys). This means co-change tracks exactly the
code files Brain 2 knows — no hardcoded language glob — so a declarative `[[corpus]]` serve
over a TypeScript codebase works without configuration.

**Mid-serve refresh.** A `CoChangeRefreshPass` (dreaming actor) rebuilds the index from recent
commits each maintenance tick and swaps it into the live `CoChangeRef` without a restart. The
planner reads the current index atomically.

**Dogfood result.** On a real TypeScript codebase, co-change surfaced GitHub-semantic coupling
that grep cannot find: symbols with no literal "github" in their names were coupled via
co-change to the integration point because they are part of the same feature. That is the
signal earning its keep.

### 4 — Gather the why (`_gather`, C-3a)

For the integration point and every node in the blast radius, the planner collects cross-hemisphere
memories via `CrossLinkIndex.memories_for()`, fetches the associated `MemoryRecord` from the
experiential store, and partitions them into:

- **Constraints** (`constraint` / `gotcha` kinds) — the load-bearing "what you must not break"
- **Context** (`decision` / `investigation` / `episode` kinds) — the recorded "why it is this way"

**Module rollup.** Cross-hemisphere links are created at module granularity today (git `diff-tree`
footprints carry file paths, not line numbers). To avoid missing file-scoped decisions, each
in-scope symbol is rolled up to its containing module (`_containing_module`) and the module's
memories are harvested too — so a decision recorded against `db.py` as a whole surfaces when any
symbol inside `db.py` is in scope. The rollup tier ranks below a direct symbol-level link in the
scoring step.

**Relevance ranking before the budget cut (C-3a).** All candidates are collected first, then
scored by deterministic behavioural signals only (§14.2 firewall — never the model grading memory
prose), then the highest-scored survive the `memory_budget` cut:

- **Proximity** to the integration point (direct link to the target > link to a radius node >
  module-rollup-only link)
- **Recency** — newer memories score higher, normalised across the gathered set
- **Importance** — the operator-set `metadata["importance"]` (default 1.0)
- **Supersession demotion** — a replaced belief is penalised so it sinks below current beliefs

Constraints/gotchas are preserved ahead of generic context under a tight budget: the full
constraint set fills first, context fills what remains.

Memories are deduped across the scope (a memory linked to both the integration point and a
radius node appears once, at its closest tier). Superseded beliefs are included but annotated with
their supersession reason and timestamp. Stale references (anchors pointing to code no longer in
the graph) are flagged. The `memory_budget` (default 30) caps total memories; those dropped past
the budget are counted.

**Coverage honesty — the load-bearing property.** Every brief carries a `CoverageReport` at
two granularities:

```
{N} of {M} in-scope node(s) have symbol-level context; {K} have NONE —
absence there is *unverified* (the brain may simply hold no memory for that code).
File-level: {J} of {L} file(s) in scope carry recorded notes — cross-links are
file-granular, so these decisions/gotchas are about the file, not pinned to an exact symbol.
```

The two granularities reflect an honest current state: symbol-level links are sparse (they require
explicit `remember` calls or line-aware footprints); file-level links are what the brain actually
records today via module rollup. **Coverage is computed before the budget cut and is independent
of it** — it reflects what the brain holds, not what fit in the brief.

A brief that printed "Constraints: none" when it means "no data here" would make the actuator
bolder exactly where the brain is blind — worse than no brief. The coverage report is what makes
the tool safe to ship with sparse memory coverage.

Each radius node in the rendered brief also carries a per-node count: `[N note(s)]` or
`[no recorded context]`, so the actuator sees at a glance where the brain does and does not have
coverage.

**Read-only.** Gather logs no Tier-1 usage signal and writes nothing, so `plan` is safe to call
in investigate mode.

---

## Assembly: `PlanBrief` and its rendered output

`PlanBrief` is a structured, frozen dataclass. `PlanBrief.render()` produces a plain-text block
organized as:

1. Integration point (kind, label, location, relevance score, ambiguity warning + alternatives)
2. Blast radius, grouped by relation: callers / subtypes / callees / co-change / container
3. Known constraints and gotchas
4. Decisions and context
5. Coverage report

**No LLM synthesis.** The brief is a structured payload. A natural-language summary that
integrates the findings into flowing prose is a **later, removable layer**, deliberately deferred
until the core is measurable. See the eval section below.

---

## Eval: `impact-eval` CLI (git-derived blast-radius recall)

How well does the blast radius actually capture the symbols that co-change in practice? The
`impact-eval` CLI measures this:

1. Re-derive the code graph (Python AST + jedi, or SCIP) from the repo.
2. Mine recent fix commits (subject lines matching fix/bug/revert/…); map their changed lines to
   code symbols via the graph's anchors.
3. For each co-changed-symbol pair in a fix commit, ask: does the coupled symbol fall in the
   target's blast radius?
4. Report recall and cross-file recall (the "forest" cases a local LLM is blind to).

**Ground truth from git — non-circular.** The eval mines co-changed pairs from commit history,
entirely independent of the experiential brain. This is not "curate a gotcha then check we
retrieve it" — the ground truth is git, not the brain grading itself.

**Co-change lift.** When `--cochange-commits N` is supplied, the eval uses a temporal split:
test pairs from recent fix commits, the co-change index built from older commits (no leakage).
It reports recall with call-graph only vs. call-graph + co-change, so the co-change lift is
directly observable.

**Recall only; precision is judged qualitatively.** The eval measures whether the coupled symbol
lands in the radius. Precision — whether every node in the radius is genuinely relevant — is
harder to measure automatically and is evaluated by inspection. A precision-aware eval is future
work.

---

## Wiring

- Exposed as the `plan` MCP tool via `build_server` in `packages/gateway/src/thalamus/gateway/server.py`
- Assembled by `build_planner` in `packages/cli/src/thalamus/cli/brain.py`, composed from the
  gateway's existing Brain-2 collaborators (graph, links, structural retrievers, views) — no
  duplicated store or encoder
- Co-change index built from `packages/cli/src/thalamus/cli/cochange.py` and
  `packages/structural/src/thalamus/structural/cochange.py`; live-refreshed mid-serve by the
  `CoChangeRefreshPass` dreaming actor (`packages/dreaming/src/thalamus/dreaming/cochange_refresh.py`),
  which swaps a freshly-mined index into the `CoChangeRef` the live planner reads
- Eval harness: `packages/cli/src/thalamus/cli/impact_eval.py`

The `plan_cochange_commits` serve parameter (default 500) controls how many recent commits the
co-change index is seeded from at startup. Set it to 0 for call-graph-only radius.

---

## Relationship to other components

- **Brain 2 auto-refresh (`StructuralRederivePass`).** The blast-radius traversal is only as fresh
  as the structural graph. Auto-refresh ensures the graph reflects current source, so a `plan`
  query does not operate on stale topology.
- **Cross-hemisphere links (§13.19).** The link from an experiential memory to a structural node
  is what makes the gather step work. Coverage of those links — how many structural nodes have
  experiential context attached — is the key quality dimension. The `CoverageReport` surfaces
  this gap honestly.
- **`StructuralRelevanceRetriever` (L-R2, §13.19).** The cross-link join now has two consumers:
  the `plan` brief (via `_gather`) and the live recall path (RRF-boosting experiential memories
  cross-linked to the cue's anchor code nodes). The plan tool is the first explicit fusion of
  both hemispheres at query time.
- **Dreaming.** `CoChangeRefreshPass` and `UsageRefreshPass` both run in the maintenance ticker
  dreaming loop, so the co-change coupling data used in blast-radius computation stays fresh
  mid-serve without a restart.
- **Universal ingestion (§16 step 2).** The "gather what the brain knows" step is only as rich
  as what has been ingested. Analysis findings, specs, and externally-produced reference material
  attached to code nodes enrich the brief when they are in Brain 2 as producers.

---

## Honest limits (the tool's nature, not bugs)

Document and communicate these — they define the correct operating model:

- **Cross-module coupling via string literals.** A string like `"github"` in a config value that
  causes one module to call another at runtime is not a graph edge. The tool will not surface it.
  Grep is the right complement for cross-module leakage through strings.
- **SQL / DB constraints.** Database schema constraints are not code nodes. They do not appear in
  the blast radius.
- **Pure greenfield seams.** When the target has no existing code node, there is nothing to
  resolve to. The brief reports `Could not resolve` — the honest salvage. The actuator still has
  the cue context; the brief adds nothing structural in that case.
- **Reporting absence.** The tool reports what exists and what depends on it. It does not report
  what is *missing* — a symbol that *should* call the integration point but does not will not
  appear in the blast radius.
- **Precision.** The eval measures recall; a very broad blast radius may include many nodes with
  no real coupling to the change. The circuit-breaker addresses the most extreme cases; qualitative
  review of the brief is still part of the workflow.

**The right operating model:** use `plan` to find the dense coupling cluster, then grep and
targeted reads for cross-module leakage through strings and SQL constraints.

---

## Open questions

- **Precision-aware eval.** Recall is measured; precision is judged by inspection. A ground-truth
  precision signal (e.g. which radius nodes the developer actually changed in the fix) would
  complete the picture.
- **NL synthesis (L3-gated).** Should the brief include a natural-language summary that integrates
  the findings into prose? Deliberately deferred: it requires a reliable eval (does the summary
  help the actuator more than the structured payload?) and is unmeasurable at the current data
  volume. The structured payload ships first; synthesis is a removable layer on top.
- **Token budget and truncation priority.** When the brief exceeds a token budget, what is the
  right truncation order? Candidate ordering: integration point → direct dependents with recorded
  context → constraints/gotchas → deeper radius → decisions/context. Not yet specified.
- **Superseded beliefs in the brief.** Currently surfaced with their supersession reason and
  timestamp. Whether the full supersession lineage belongs in the brief (the history may matter
  for planning) or only the current belief is an open UX question.
- **Co-change window.** Larger `plan_cochange_commits` gives more coupling signal but slower
  startup. The right default for different codebase ages has not been systematically tuned.
