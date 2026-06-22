# Thalamus — Outstanding Work & Ranked Roadmap

*The single backlog of everything still to do, so nothing gets lost. Complements — does not
replace — [`design-notes.md`](design-notes.md) §16 (the canonical capability roadmap) and the
deep-dives (per-area specs). This file is the **ranked, actionable superset**: every item carries a status, what it
does, why, the concrete steps, its gate/dependency, and where it's spec'd.*

*Last updated 2026-06-16.*

---

## How to read this

There are **two parallel tracks**, plus supporting work:

- **Track L — Learning & Credibility** (the thesis). Does the brain actually get *more useful with
  use*? This is the project's central blocker and the **highest priority**. Spec: §13
  [`outcome-learned-retrieval.md`](deep-dives/outcome-learned-retrieval.md) +
  [`dreaming.md`](deep-dives/dreaming.md).
- **Track C — Capability & Ingestion** (the planning brain). The forest-for-the-LLM features we've
  spec'd: producers, the `plan` tool, multi-language. Spec: §16 + [`planning.md`](deep-dives/planning.md)
  + [`structural-hemisphere.md`](deep-dives/structural-hemisphere.md).
- **Track R — Retrieval & infra hardening**, **Track M — Measurement/eval**, **Track D — Brain-1
  data quality**, **Track I — Instrumentation & event-store architecture** (new 2026-06-17), **Track
  S — Security** (§17 [`security.md`](deep-dives/security.md), tracked but deliberately lower).

**Two cross-cutting truths that drive the ranking:**

1. **Building *is* using.** The learning signal (Tier-1 usage + Tier-2 outcome) is a *byproduct of
   real dev work done through the brain*, not of standalone queries. So Track-C work, done *through*
   the brain, **feeds** Track L with the dogfood volume + negatives it's starved of. They are not
   competing — capability work is also data generation.
2. **Credibility C is gated on B is gated on data.** The learned re-ranking payoff (L-C) cannot be
   trusted until the proxy↔truth monitor can discriminate (L-B), which needs *negatives* in the data
   (L-A loop / friction capture). Shipping a feedback layer you can't measure is the exact self-validation
   trap. So the learning track's **near-term** work is *un-blocking the measurement*, not the fancy
   ranker.

**Status legend:** `built` (done, live) · `partial` (skeleton exists, gaps noted) · `design`
(spec'd, no code) · `idea` (noted, not yet spec'd).

---

## ★ Ranked next steps (the short list)

If we did things strictly in order, this is it. Rationale follows each.

*Re-aimed 2026-06-15: the learning track now targets **relevance credibility** (usage + supersession +
recency + structure), which accrues in this workflow — the **outcome** loop (churn / monitor /
counterfactual) is **parked** (no data source here). See Track L.*

| # | Item | Track | Why this rank |
|---|------|-------|---------------|
| ✅1 | ~~Usage-weighted retrieval rung (L-R1)~~ | L | **SHIPPED** (`f5153a6`) + mid-serve refresh + a first cut of L-R2. |
| ✅2 | ~~`plan` / impact tool (C-3)~~ | C | **SHIPPED + dogfooded + fixed**. |
| ✅3 | ~~Findings producer v1 (C-1)~~ | C | **BUILT** (retrievable corpus). v2 = fuse into the plan radius (C-1/C-2/C-3b). |
| ✅4 | ~~Today's plan fixes + findings producer~~ | C | **COMMITTED** `5542954` (gather blindness + co-change flood fixes + findings v1). |
| ✅5 | ~~C-3a — gather ranking~~ | C | **Built** (validated live: tight budget keeps the highest-value memories, constraints prioritized). |
| ✅6 | ~~C-2 + C-7 — cross-link layer~~ | C | **Built** (annotates edges + symbol-level resolution; validated live over 1,917 nodes). C-3b still open. |
| ✅7 | ~~L-R2 — structural-centrality weighting~~ | L | **Built** (validated live: 181/243 memories weighted, hubs float up). |
| ✅8 | ~~C-3b findings-in-brief + C-8 line-aware footprints + M-2/M-4 eval~~ | C/M | **COMMITTED** `bc17a1e`/`a6ab5fd`; rung verdict acted on (centrality leads). |
| ✅9 | ~~Track I (I-1 attribution-pass, I-2 retention, I-3 Architecture B)~~ | I | **DONE + live-validated**. Brain reads its own usage from Neo4j, not files. |
| **1** | **Finish Architecture-B loose ends** *(optional)* | I | Real log disposal (I-2 drops consolidated segments) + consolidate attribution/outcome into the brain the same way. Completes the architecture; non-blocking. |
| **2** | **M-1 — brain-on/off thesis ablation** | M | The big unmeasured question: does a brain-informed actuator make fewer cross-cutting mistakes? Hardest; the project's central claim. |
| — | **PARKED:** the outcome loop (churn / monitor / counterfactual) | L | Built + gated but dormant — no captured code outcomes in this workflow. Kept for if instrumented coding resumes. |

After these: the re-ranker (credibility C, gated on the parked outcome loop), multi-language
ingestion (C-4), retrieval hardening (Track R), then the security workstream (Track S). Full detail below.

---

## Track L — Learning & Credibility  *(highest priority)*

> **⮕ RE-AIMED 2026-06-15: relevance credibility, not code outcomes.** The learning target is now
> "which memories are **current / used / important / well-connected**" — surfaced better over time —
> NOT "did committed code survive / tests pass." Reason: the outcome signal **doesn't accrue in this
> workflow** (no test capture; much non-code work), while **usage + supersession + recency + structure
> accrue every session**. The signals & build (this track):
>
> - **Supersession demotion** — current beliefs outrank replaced ones ("don't serve me the old plan").
>   *Built; verified working.*
> - **Recency** — newer-on-a-topic ranks up. *Built (L0).*
> - **Usage / cross-session reuse** — recalled-and-used memories rise (the "reliably-useful core").
>   *Partly built (usage_stability).* → **next:** a usage-weighted retrieval rung.
> - **Structural centrality** — memories well-connected to Brain-2 knowledge. *Links built; weighting next.*
>
> **Firewall (held):** elevate by external/behavioral facts only (used / superseded / recent / central),
> **never** the model grading its own memory prose (the self-validation trap). Using top-ranked memory *content* to
> inform a plan is fine; scoring memory *quality* from text is not.
>
> **PARKED — the outcome loop** (churn / `session_fate` / `session_struggle` / proxy↔truth-on-commits):
> built and gated, but dormant (no data source in this workflow). Kept for if heavy *instrumented*
> coding ever happens. Detail preserved in [`deep-dives/learning-loop.md`](deep-dives/learning-loop.md)
> (now redirect-bannered). What carries over: the **firewall**, the **modularity seams**, and the
> **research toolkit** (SNIPS / anytime-valid CIs / ranking) — they re-target onto the usage ranker.

> *Items **L-1 … L-5** below detail the now-**PARKED** outcome loop — kept for reference and for if
> instrumented coding resumes. The active work is the relevance signals in the blockquote above
> (usage-weighted rung next). The next active build item is **L-R1** (immediately after L-5).*

### L-1. Tier-2 join starvation — `LARGELY RESOLVED` · ✅ *(verified live 2026-06-17)*
- **The old "`n_units≈1` forever" framing is obsolete.** `classify_outcome` now blesses the
  green-tests-then-commit flow → PASSED (`experiential/outcome.py`), and footprint **attribution**
  (the primary durable usage signal, `usage_attributed.jsonl`) works. **Live `thalamus verdict`:**
  Tier-1 utility@5 0.152; **proxy↔truth n_units=20, coverage 0.95, alignment +0.125** (proxy tracks
  truth), reward-hacking False; 27 sessions Tier-2-labelled. The join/keying is **not** the bottleneck.
- **Residual (the real remaining gap):** **negatives are thin** — only 1 fate-negative, so
  `utility | failure` can't discriminate yet. That's the parked outcome-loop / negative-labeler
  (L-2), and negatives are scarce by nature in fix-forward dev → it *accrues with use*, not a build.
- **Spec:** `cli/{verdict,attribute}.py`, `experiential/outcome.py`.

### L-2. The negative-signal labeler (survival-vs-overwrite) — `partial` · **P0** · *step 1, the real first build*
- **What:** A region-level labeler over git diffs + the trajectory log that fills the stubbed
  `churn_ratio` / `survived_activity` fields in `FateContext`. **The negative is the rewrite, not the
  revert** (fix-forward has no reverts). Signals, cleanest first: soft-revert (added-then-deleted
  lines), same-region thrash, fix-shaped commits on recent code, within-session red-test struggle,
  cross-session fix-linkage. Positive = survival (committed, not re-touched, shipped, stayed).
- **Why:** Credibility step A computes over an empty input today — *this* is why it's inert, not the
  missing re-ranker. This is the primary negative well, not a weak one.
- **Do:** Build the labeler; extend `assess_fate` to weight churn/survival; confirm the credibility
  distribution gains real spread (not all-UNKNOWN).
- **Gate:** none; the first real credibility build. Subsumes the old "review friction" idea (L-4).
- **Spec:** **[`learning-loop.md`](deep-dives/learning-loop.md) Step 1**; §13.7; `experiential/fate.py`.

### L-3. Un-blind the proxy↔truth monitor (credibility B) — `design` · **P1** · *step 2*
- **What:** Make the monitor *able to discriminate*, now that L-2 supplies negatives: deliberate serve
  restart so supersessions/usage record; surface `monitor_with_fate` vs `monitor_without_fate`.
- **Why:** Proves the proxy isn't lying *relative to fate*. **Honest limit:** still observational
  (outcomes co-occur with recalls, not randomized) — B does NOT establish causation. That's L-4's job.
- **Do:** Run the rollout; capture ≥5–10 keyed sessions with real outcomes; read alignment +
  reward-hacking flags; document what discriminates (even "no signal yet" is a real read).
- **Gate:** needs L-1 (keys) + L-2 (signals) to be worth running.
- **Spec:** **[`learning-loop.md`](deep-dives/learning-loop.md) Step 2**; dreaming.md; §13.18.

### L-4. The counterfactual instrument (calibrated exploration) — `design` · **P1** · *step 3, decides truth*
- **What:** The causal layer: temporal hold-out (credibility at T predicts *future* fate at T+1),
  recall-time interleaving / calibrated exploration (perturb the served set, log real propensity,
  measure outcome correlation — per-recall counterfactuals), and session-level on/off A/B.
- **Why:** Observational logs (even with negatives from L-2 and a working monitor from L-3) only show
  *correlation*. Without counterfactuals you cannot tell whether the brain *caused* the outcome — and
  you cannot validate the re-ranker non-circularly. **This is what makes the thesis provable rather
  than plausible.** Promoted into the credibility spine from "later."
- **Do:** Build temporal hold-out first (cheap, offline, non-circular); then recall-time interleaving
  (makes `propensity` real — see R-7); then session A/B.
- **Gate:** needs L-1 (keys). Is itself the gate on L-5.
- **Spec:** **[`learning-loop.md`](deep-dives/learning-loop.md) Step 3**; §13 calibrated exploration.

> *(The old "review friction" item is folded into L-2 — within-session struggle is signal #4 of the
> labeler. Reverts are deliberately NOT built: they ~never fire in fix-forward dev.)*

### L-5. The re-ranker (credibility C) — `design` · **P2** · *step 4, last & most-gated* *(gated on L-2 + L-3 + L-4)*
- **What:** A durable per-memory credibility store; an ablatable credibility-aware retriever rung;
  belief reconciliation consumes it. "The brain reorganizes itself."
- **Why:** The actual learning *adaptation* — the thesis made visible.
- **Do:** Persist credibility (today `CredibilityPass` only logs the distribution); add the rung behind
  the `Retriever` seam. Measure two ways: **L1** (surfaces better-*future*-fated memories — temporal
  hold-out via `compare`, NOT the fate it trained on) + **L3** (improves outcomes — needs L-4).
- **Gate:** **hard-gated on L-2 (input) + L-3 (monitor) + L-4 (a non-circular validation).** A
  fate-trained ranker measured by a fate metric is circular — the quiet self-validation trap. Doing C before
  L-4 exists is the thing not to do.
- **Spec:** **[`learning-loop.md`](deep-dives/learning-loop.md) Step 4**; dreaming.md; `dreaming/credibility.py:10-12`.

### L-6. The learned outcome-weighted ranker — `design` · **P2** *(gated on L-3, L-5)*
- **What:** The frontier §9.2 layer: a removable retrieval rung trained on real usage/outcome logs
  that outranks the boring baseline on the eval harness.
- **Do:** Define the metric ablation first (§14.4/§14.5); train on the off-policy log
  (`features` are already captured per candidate); prove it beats L0+hybrid before trusting it.
- **Gate:** needs a discriminating monitor (L-3) + enough volume.
- **Spec:** §9.2, §13.4 (bent-geometry variant), §13.11.

> ── **ACTIVE: relevance-credibility direction** (the re-aim; feedable in this workflow) ──

### L-R1. Usage-weighted retrieval rung — `built` · ✅ *(2026-06-15)*
- **What:** `UsageWeightedRetriever` (`retrieval/usage_weighted.py`) lifts memories by **behavioral
  cross-session usage** (`reuse_by_memory` — distinct sessions recalled-and-used in), RRF-fusing the
  relevance rank with a usage rank (mirrors `HybridRetriever`; preserves native score; ablatable).
  Re-orders only the relevance pool, so it can *promote* a used memory but never summon an irrelevant
  one. Wired into the gateway (`usage_weighting`/`usage_weights`, policy tag `+usage`) and serve
  (computes weights from the durable logs at startup; off for a cold brain / investigate mode).
- **Verified on real logs:** 20 memories carry a usage weight; top used in 7 sessions → recall lifts
  them on restart. **Firewall:** behavioral act, never the model grading prose. Suite 464 passed.
- **VALIDATED (2026-06-17, utility-join `rung-eval`, de-leaked temporal split):** a **real** lift —
  survives the de-leak (past usage predicts future use) — but with an **intrinsic recall@k tradeoff**
  (over-promotes used-but-not-yet-popular memories; `weight 0.5≈1.0`, not tunable away). Big win on the
  process-heavy brain (this repo); a recall wash on a code-rich sample project. **Live: kept ON (inner), behind
  `usage_weighting`** so recall-sensitive deployments can disable it. The lever is enable/disable per
  workload, not weight-tuning.

### L-R2. Structural-centrality weighting + Brain-2 correlation — `built + validated` · ✅ *(2026-06-17)*
- **What:** Lift memories well-connected to the structural graph (summed degree of cross-linked code
  nodes) — the query-independent relevance-credibility signal. Two legs shipped: a query-local
  `StructuralRelevanceRetriever` (`f5153a6`) and the global `StructuralCentralityRetriever`.
- **VALIDATED (rung-eval utility-join):** **global centrality is the clean winner** — lifts recall AND
  MRR on both brains, no tradeoff, non-circular (graph topology ⟂ usage labels) → **applied OUTERMOST
  (leads the live ranking)**. The **query-local structrel leg earns ~nothing** on both brains →
  **dropped from the live chain** (`structural_relevance=False`, kept behind the flag for a rework).
- **Open:** structrel rework (or retire); the `+full` stack underperforms centrality-alone when usage
  is stacked on a code-rich brain (don't blindly stack — see L-R1's recall tradeoff).

### L-7. Belief supersession beyond D1 (D2 AST-drift, D3 semantic) — `partial` · **P2**
- **What:** Today supersession is explicit-only (`remember --supersedes`, D1). D2 = detect stale
  beliefs when their footprint code drifts (AST); D3 = semantic-contradiction detection. `BeliefAudit`
  is currently file-vanished-only and proposes to the dream log, never auto-applies.
- **Why:** Beliefs silently rot; the brain should *flag* drift (conservatively — propose, never
  delete).
- **Do:** Add AST-drift detection to the dreaming pass; surface proposals on recall (not just the
  log). Keep §14.4 discipline (coexist/flag, never auto-supersede).
- **Spec:** §13.18 D2/D3; code: `dreaming/belief_audit.py`.

### L-8. Dreaming cross-cycle oscillation damping — `idea` · **P3**
- **What:** Reweighting + re-segmentation each cycle can make credibility/segments bounce. "Not
  designed yet."
- **Do:** Design hysteresis/damping before C ships at scale.
- **Spec:** dreaming.md open questions.

---

## Track C — Capability & Ingestion  *(the spec'd planning-brain work)*

> The producer registry (just shipped) is the seam these plug into. §16 build order: hybrid ✅ →
> universal ingestion ✅ → **#3 multi-language → #4 findings → #5 plan → #6 research**.

### C-1. External-analysis findings producer — `built (v1)` · ✅ *(2026-06-16)*
- **What:** `FindingsIngestor` + `parse_findings` (structural pkg) accept **SARIF** and a **generic
  JSON** → one `finding` `StructuralNode` per result; `FindingsProducer` (kind `findings`) registered
  in the producer registry; surfaced under "## Related findings" in recall. Demo-confirmed live (BGE).
- **v1 = retrievable corpus** (nodes anchor to the findings FILE, real src location in label/metadata).
  Wired into a sample project's `thalamus.toml` (empty until a SARIF/JSON file is dropped + serve restart).
- **v2 follow-on (→ C-3b):** finding→code `annotates` edges so findings appear in a plan blast radius.
  **Depends on C-2.**
- **Spec:** §16 step 4; structural-hemisphere.md (deferred producers list).

### C-2. Anchor-based cross-linking of non-code nodes to code — `built` · ✅ *(2026-06-16)*
- **What:** Today non-code corpora (docs, text, findings) surface only via *direct* retrieval,
  correctly tagged by corpus. To make a finding/doc *fuse* into a code node's context (and the `plan`
  brief), link non-code nodes to code by `SourceAnchor`. Only then does the `corpus="code"` default
  in the gateway cross-link path (`payload.py from_node`, gateway `_structural_for`) matter.
- **Why:** Prerequisite for findings/docs to show up as "what the brain knows about this code," which
  is the core of the `plan` tool. **Now the near-term unblock** — findings v1 ships but can't fuse.
- **Do:** Add anchor→code-node resolution in the cross-link layer; generalize the corpus tag in the
  cross-link path.
- **Gate:** unblocks C-1 v2 fusion and C-3b.
- **Spec:** §13.19; structural-hemisphere.md (deferred).

### C-3. `plan` / impact tool (v1) — `built` · ✅ *(2026-06-16, committed e8f878f/4594c44/9ea4c88)*
- **What:** Given a target → resolve integration point (whole-graph exact-name lookup, then
  code-preferring semantic) → edge-typed, budget-bounded blast radius (callers / subtypes / callees /
  git co-change / container, hub circuit-breaker) → gather cross-linked decisions/gotchas → one
  coverage-honest fused brief. Deterministic core; NL synthesis deferred. Live as a read-only `plan`
  MCP tool; co-change refreshed mid-serve by a dreaming pass; git-derived `impact-eval` measures recall.
- **Dogfooded + fixed (2026-06-16):** beat grep on a real sample-project task (GitHub-semantic coupling);
  today's Python-brain run drove the gather/co-change fixes below.
- **Honest limits (inherent, not bugs):** no cross-module string-literal coupling; can't report what's
  *missing*; no SQL constraints; greenfield targets mis-anchor. Model: "plan finds the dense coupling
  cluster; grep/reads for the rest."
- **Spec:** [`planning.md`](deep-dives/planning.md); §16 step 5.

### C-3a. Plan gather — relevance ranking before the memory budget — `built` · ✅ *(2026-06-16)*
- **What:** With the gather rollup working, the live brief hits `memory_budget=30` and **omits the
  rest with no ranking** (e.g. on `_compute_radius`: 30 surfaced, 40 omitted, trimmed in traversal
  order). Rank gathered memories before the cut — by recency, importance, usage (L-R1 weights),
  supersession (demote replaced), and cross-link proximity (integration point > radius node) — so the
  surviving set is the *most* relevant, not the first encountered.
- **Why:** The difference between "surfaces context" and "surfaces the *right* context"; reuses the
  Track-L relevance signals already built.
- **Spec:** `gateway/planner.py` `_gather`; ties to L-R1/L-R2.

### C-3b. Findings (and docs) in the plan blast radius — `design` · **P1** *(findings v2 — now unblocked)*
- **What:** C-2 has landed finding→code `annotates` edges; fold the annotating finding/doc nodes into
  the plan brief (follow `annotates` edges from the in-scope code, mirroring `_add_cochange`/`_gather`)
  so the brief shows "what's already flagged here."
- **Likely shape:** a **separate brief section** ("Known findings in scope"), not a radius *relation*
  (findings are "already-flagged," not "what breaks").
- **Gate:** ✅ unblocked — C-2 (annotates edges) + C-1 v1 both landed. The next concrete plan-tool build.
- **Spec:** planning.md; §16 step 4–5.

### C-4. Multi-language structural ingestion (tree-sitter producer) — `design` · **P2**
- **What:** A lightweight syntactic (tree-sitter or equivalent) ingestor as a producer, for
  languages whose SCIP indexer is unavailable/too heavy.
- **Why:** Broadens Brain-2 coverage; the sample project is TypeScript (765 files) and today only SCIP covers
  it — a tree-sitter producer is a lighter path. Language becomes a plugin axis.
- **Do:** `register_producer` a tree-sitter ingestor; map its nodes/edges to the open schema.
- **Gate:** none (drop-in on the seam); prioritize behind findings + plan.
- **Spec:** §16 step 3; structural-hemisphere.md (deferred).

### C-5. `research` tool — `design captured, not built` · **P3**
- **What:** Deeper cross-hemisphere "what do we know about X" synthesis — entry is a *question/topic*
  (no code anchor); broad cross-hemisphere retrieval → synthesis. The complement to `plan` (which is
  target→radius): research is *understanding before you know where to start*.
- **Design captured** (2026-06-16, was a lost design discussion — now durable): full plan-vs-research
  distinction + the two honest tensions + the recommendation in
  **[`deep-dives/research.md`](deep-dives/research.md)**.
- **Verdict:** don't build as a headline yet — its core is **synthesis** (unmeasurable, L3-gated, the
  part we deferred for plan) and **reference breadth** (the brain is thin on docs/findings today).
  Preconditions: more Brain-2 content + an eval (cf. M-2). First step when ready = a **thin experiment**
  (deep multi-pass recall + organize, no synthesis). Today's C-2/C-3a/L-R2 already build its substrate.
- **Spec:** [`deep-dives/research.md`](deep-dives/research.md); §16 step 6.

### C-6. Producer entry-point / plugin auto-discovery — `idea` · **P3**
- **What:** External plugins register producers via entry points (the in-process registry is the v1
  seam).
- **Spec:** structural-hemisphere.md (deferred).

### C-7. Symbol-level cross-linking (finer than module) — `built — seam only, gated on line footprints` · ◑ *(2026-06-16)*
- **What:** Cross-links are created at **module granularity** (`structural.linking` links a memory to
  the *module* of each touched file — git's per-file diff is the finest footprint today). Recall
  bridges to symbols via k-hop spread; the plan gather now bridges via a module-rollup (today's fix).
  Both are *bridges*. Finer linking — map a memory's diff lines → the enclosing **symbol** — would
  make per-node coverage real (symbol-level `nodes_with_context` instead of always ~0) and retire the
  rollup. Proven live: 797 cross-links, 100% on module nodes, 0 on symbols.
- **Why:** The honest end-state for coverage; the rollup over-attributes a file's notes to every symbol.
- **Status (2026-06-16):** the **seam is built** — `link_by_footprint` accepts `(file, lines)`
  and resolves to the smallest enclosing symbol via the new `SymbolResolver` (validated live: line
  410→`_lexical_resolve`, 470→`_blast_radius`, module fallback when no line). But it **stays module-level
  in practice** because live footprints carry no line data (see C-8). The machinery is proven correct
  and lights up the moment line footprints exist.
- **Gate:** depends on **C-8** (line-aware footprints) to actually produce symbol-level links live.
- **Spec:** §13.19 ("symbol-identity re-resolution" — the deferred layer); `structural/linking.py`,
  `structural/symbol_resolution.py`.

### C-8. Line-aware footprint capture — `idea` · **P2** *(new 2026-06-16, the keystone)*
- **What:** Episode footprints are git per-file diffs (`payload["files"]`) — **paths only, no lines**.
  Capture the touched line ranges per file so a footprint becomes `(file, lines)`.
- **Why:** The single unlock that upgrades **both** C-7 (symbol-level cross-links) and **L-R2**
  (symbol-degree centrality instead of module-degree) from "seam built" to "fully effective." Without
  it, both run at module granularity.
- **Do:** Extend the episode/footprint producer (`experiential/episode.py`) to record diff line ranges;
  thread them through `link_by_footprint` (already accepts the shape).
- **Spec:** §13.19; `experiential/episode.py`, `structural/linking.py`.

---

## Track R — Retrieval & infra hardening

### R-1. Persistent lexical inverted index — `partial` · **P2**
- **What:** `LexicalRetriever` is O(corpus) per query (full `store.scan` + re-tokenize every recall).
  Fine at current sizes; swap a persistent inverted index later.
- **Spec:** retrieval/lexical.py (documented deferral).

### R-2. Meaningful structural relevance floor — `partial` · **P2**
- **What:** `structural_min_relevance` defaults to 0.0 (filters only exact-zero cosine). Tune a real
  BGE floor from usage logs so direct structural hits aren't noisy.
- **Spec:** gateway.py:188-190.

### R-3. Calibrate cross-link injected scores — `partial` · **P2**
- **What:** `StructuralLinkedRetriever` injects magic constants (2.0 seed / 1.9 expanded) that always
  dominate relevance scores. Intentional but uncalibrated.
- **Spec:** gateway.py:85.

### R-4. Single-transaction Brain-2 swap — `design` · **P2**
- **What:** `StructuralRederivePass` does remove-then-MERGE; a recall in the sub-second window can see
  partial state (self-heals next call). A single-transaction swap removes the window.
- **Spec:** structural_rederive.py; structural-hemisphere.md (deferred robustness).

### R-5. Focus resolution beyond `module:` — `partial` · **P3**
- **What:** `_focus_node_ref` only ever produces a `module:` ref, so a focus naming a function/class
  won't anchor directly.
- **Spec:** gateway.py:44-48.

### R-6. Per-corpus doc index persistence (Neo4j) — `partial` · **P3**
- **What:** Code index is Neo4j-persisted; doc/text indexes rebuild at startup. Persist them for fast
  warm-starts at scale.
- **Spec:** structural-hemisphere.md (deferred).

### R-7. Off-policy propensity (IPS) — `BUILT (serving + logging)` · ✅ *(2026-06-17; gate 611 passed)*
- **What was the gap:** `propensity=1.0` (deterministic top-k) made off-policy eval (IPS) *undefined*
  (no common support), not just noisy — so the per-recall counterfactual the thesis ablation eventually
  needs couldn't be built. The **logging** half is irreversible-if-deferred (can't reconstruct a
  propensity after the fact).
- **Built:** `retrieval/exploring.py` `ExploringRetriever` + pure `explore_selection` — a two-policy
  **mixture** (with prob 1−ε serve the deterministic top-k; with prob ε serve a uniform random k-subset
  of the top-`pool`) with an **exact per-item marginal propensity** stamped into each shown item's
  `features["propensity"]`; `LoggingRetriever` now logs *that* instead of a hardcoded 1.0. Wired through
  `build_two_hemisphere_gateway` (`explore_epsilon`/`explore_pool`) + serve (`--explore-epsilon` /
  `--explore-pool`). **Off by default (ε=0 ⇒ deterministic top-k, propensity 1.0 — live recall
  unchanged);** off in investigate. 6 tests (exact propensity, common support, the propensity reaches
  the log end-to-end). Uniform-within-pool is the boring-exact v1; a Plackett-Luce explorer is the refinement.
- **Deferred (the estimator, NOT this):** the IPS/SNIPS estimator + anytime-valid CIs over logs
  collected with ε>0 — that's the M-1 long-game, gated on volume. This builds the *logging substrate* now.
- **Spec:** `retrieval/exploring.py`, `instrumentation/logging_retriever.py`, `cli/brain.py`, `cli/serve.py`.

### R-8. Misc structural coverage — `partial` · **P3**
- Router protocol has no impl (intent routing deferred). · Nested defs not indexed (AST v0). ·
  Cross-module inherit/import is a Python-AST heuristic (SCIP covers it). · `scip-python` upgrade for
  precise Python calls. · Bent-geometry retrieval (§13.4) gated on confirming the recall-miss
  hypothesis from logs.

### R-9. Recall precision — the reliably-ignored set — `DIAGNOSED → premise refuted` · ✅ *(2026-06-17)*
- **What it was:** The live verdict shows **52 of 75** memories surfaced ≥2× are reliably *ignored*.
  R-9 assumed this was junk the rungs (centrality/usage) should demote.
- **Diagnosis (read-only probe, dogfood brain):** the "ignored" signal is **~87%
  measurement artifact, 0% demotable junk.** Decomposition of the 52: **5** stale not-in-store ids,
  **20** in-store but *footprint-empty* high-importance curated memories (vision roadmap, firewall
  constraint, dogfood discipline) — `used=bool(∅)=False` in `FootprintAttributor` **by construction**,
  **20** footprinted-but-citation-only (stale-attribution gap), leaving only **7** genuine residual,
  themselves foundational architectural memories whose value is *orientation*. Two `used` definitions
  are structurally blind to conceptual recall: footprint overlap (credits only code-overlapping
  memories) and citation overlap (`|mem∩out|/|mem|≥0.5`, denominator penalizes long memories — only
  **1.1%** of 534 live citation signals clear 0.5).
- **Verdict:** **do NOT build a ranking fix** — demoting the "ignored" set would demote the firewall /
  vision / discipline (a Goodhart trap). R-9-as-framed is dead. The real work is measurement honesty
  (Track I below) + a firewall-clean credit path for conceptual recall. The
  shipped rung verdict ("centrality wins") is **scoped, not invalid**: it was struck on
  footprint-dominated labels on *both* brains (`cases_from_usage`), so it means "centrality best ranks
  *code-footprinted* memories for *code-touching* sessions" and is silent on conceptual recall.
- **Spec:** `eval/stability.py`, `eval/benchmark.py`, `instrumentation/usage.py`, `structural/attribution.py`.

---

## Track I — Instrumentation & event-store architecture  *(surfaced 2026-06-17)*

> **The reframe:** the learning loop reads the brain's *own behavioral history* (recalls, usage,
> outcomes) out of **loose append-only JSONL files outside the brain** — `retrieval.jsonl`,
> `usage.jsonl`, `trajectory.jsonl`, `dream.jsonl`. Knowledge is correctly in Neo4j (`store.add`);
> these are the *event/telemetry stream*, a different data type. Separating telemetry from knowledge
> is right (§14 firewall made physical; the flat log survived the Neo4j-wipe incident). **What's wrong
> is that the replay buffer has become the system of record for learning:** no consolidation into the
> brain, no retention, no index — so the brain must be "updated from outside itself" every cycle.
> Target the hippocampal design the project already espouses: **cheap volatile capture → dreaming
> consolidates events into the brain's own queryable state → learning passes read brain state, not raw
> files → the flat log demotes to a rotatable write-ahead buffer.** Do **not** move this into Neo4j as
> a high-write time-series (wrong fit); if it outgrows files, an owned indexed event store (SQLite-class)
> is the step, **never** the graph DB that holds the brain.

### I-1. Attribution as a maintenance pass (not a manual CLI) — `BUILT` · ✅ *(2026-06-17; gate 591 passed)*
- **What:** `usage_attributed.jsonl` was a re-derivable view yet only produced by the manual
  `thalamus attribute` CLI → **stale since May 31**, silently dragging down the usage rung (which
  re-reads it each tick) and every `verdict`. Now an **`AttributionRefreshPass`** (dreaming actor,
  generic, mirrors `UsageRefreshPass` — injected recompute/apply seams, firewall-clean) re-derives the
  footprint signals each maintenance tick from the **live gateway graph** + the logs and swaps them
  into an **`AttributedSignalsRef`** the usage rung reads (no file round-trip), also rewriting the log
  for the offline tools. Runs after re-derive/re-link, **before** usage-refresh (which consumes it).
- **Key correctness choice:** reuses `gateway.graph` (not a flat-config `build_code_graph` re-derive) —
  the declarative `[[corpus]]` path leaves `config.code_language` at its default, the same flat-config
  trap that once silently emptied co-change. Skipped in investigate mode (no log writes).
- **Validated live (dogfood `thalamus dream`):** `attribution-refresh (actor): refreshed footprint
  attribution: 555 signal(s)` ran in DAG order; deterministic (same 555 as the manual run). The manual
  refresh also moved the verdict: utility@5 0.15→0.28, reliably-used 1→15.
- **Deferred:** per-tick gating (recompute only when retrieval/trajectory logs grew) — matches the
  other refresh passes which recompute fully each tick; cheap on current brains.
- **Spec:** `dreaming/attribution_refresh.py`, `instrumentation/usage.py` (`AttributedSignalsRef`),
  `cli/serve.py`, `cli/dream.py`. **Serves need a restart to pick it up.**

### I-2. Log retention / rotation / compaction — `BUILT` · ✅ *(2026-06-17; gate 598 passed)*
- **What was wrong:** all four event logs were **append-only with zero retention** → unbounded growth.
- **Built:** `instrumentation/rotation.py` — `rotate_log(path, max_bytes, keep)` renames an oversized
  log to a numbered archive (`<name>.1`, older = higher index), shifting archives and dropping beyond
  the `keep` retention window; `jsonl_segments` lists the segments oldest-first. `read_jsonl` (the one
  reader all of retrieval/usage/trajectory funnel through) now **concatenates segments**, so the
  retained history is read back whole — rotation is invisible to `verdict`/attribution/L-6.
  Concurrency-safe with `append_jsonl`'s reopen-per-append (a rename + the next append recreates the
  live file, no lock). Wired as a **housekeeping phase** on `MaintenanceTicker` (a sibling of capture —
  writes the filesystem, not derived views, so NOT a `DreamingPass`), run on the periodic wake before
  capture, failure-isolated. Config: `--log-max-bytes` (default 64 MiB, 0 disables) / `--log-keep`
  (default 8) → ~0.5 GiB/log ceiling. 7 tests (5 rotation/segment-read + 2 ticker housekeeping).
- **Honest note:** `keep` is a deliberate retention bound (the "no silent caps" discipline) and a
  **stopgap** — it holds until I-3/Architecture B consolidates events into the brain and makes the raw
  log disposable (then dropped segments are redundant, not lost signal). `dream.jsonl` is rotated too;
  its observability reader stays on the live segment (archives preserved on disk).
- **Spec:** `instrumentation/rotation.py`, `instrumentation/_jsonl.py`, `dreaming/runtime.py`, `cli/serve.py`.

### I-3. Behavioral events intrinsic to the brain — `BUILT + LIVE` · ✅ *(2026-06-17)*
> **Increment 2 — BUILT + validated live (gate 605 passed/21 skipped):** backed up the brain (269
> records) → `Neo4jBehavioralStore` (additive label `M_behavioral_use`, one MERGE'd node per
> `(memory_id, session_id)` used-pair — idempotent, never touches `M_experiential`) → wired the
> consolidation pass into the serve (after attribution-refresh, before usage-refresh) → **shadow-
> validated** (brain `usage_weights()` == the file-derived weights, EXACTLY, 44 memories) →
> **cut `_recompute_usage_weights` over to read the brain**. Live `thalamus dream`:
> `behavioral-consolidation … 46 memory(ies)` then `usage-refresh … 46` reading from the brain. The
> usage rung no longer recomputes from a file scan — the brain reads its own behavioral history.
> Investigate mode stays read-only (in-memory store, no behavioral writes). **Serves need a restart.**

- **Resolved — (b) graph-native** — the brain accumulates its own behavioral usage as
  durable state, consolidated from the log WAL by a dreaming pass; the rung reads it from the brain, not
  a file scan; raw logs become a disposable write-ahead buffer. NOT (a) SQLite. Hot-write volume stays
  out of Neo4j because consolidation is a periodic batched fold, not per-event writes.
- **Design:** the brain is the **accumulator**. `record_usage` unions per-memory used-session SETS, so
  it is idempotent — re-folding the same logs, or re-folding a *subset* after rotation drops old
  segments, never double-counts and never loses signal. So a **durable** store makes logs disposable
  with **no cursor needed for correctness** (a cursor is only a later efficiency optimization).
- **Increment 1 — BUILT (additive, zero live-brain/schema change; gate 605 passed):**
  `experiential/behavioral.py` — `BehavioralStore` protocol + `InMemoryBehavioralStore` +
  `consolidate_usage`; `experiential/fate.py` — `usage_sessions_by_memory` (the session-set unit;
  `reuse_by_memory` now delegates to it, behavior-preserving); `dreaming/behavioral_consolidation.py` —
  `BehavioralConsolidationPass` (actor, injected seam, firewall-clean, mirrors the refresh passes).
  8 tests incl. the disposability property (subset re-consolidation keeps prior signal). The swappable
  seam everything plugs into; nothing wired live yet.
- **Increment 2 — NEXT (the live/risky part; gated on a `thalamus backup`):** `Neo4jBehavioralStore`
  (additive label, doesn't touch `M_experiential`), serve wiring, **shadow-validate** (brain weights ==
  file-recomputed weights) before cutover, then repoint `_recompute_usage_weights` at the brain and let
  I-2 rotation drop consolidated segments. Backup first (the Neo4j-wipe incident).
- **Spec:** `experiential/behavioral.py`, `dreaming/behavioral_consolidation.py`; ties to L-R1 (the
  consumer), I-1 (the sibling pass pattern), I-2 (disposability closes the loop).

---

## Track M — Measurement / eval

### M-1. L3 brain-on vs brain-off ablation — `design EXAMINED (3-expert panel)` · **P2** *(2026-06-17)*
- **What:** The only metric that validates the thesis: does a brain-informed actuator make fewer
  cross-cutting mistakes on real tasks? Hardest to measure; confounded by easier-tasks/changed-user.
- **Panel verdict (convergent):** the naive "gotcha-avoidance ablation" is **NOT the thesis test** and
  as-proposed is **circular** (if the memory is the warning, injecting it = injecting the answer;
  curating cases the brain holds conditions on it winning; can't produce a negative result). Reframe it
  as **M-1a — a conversion/delivery probe** (does surfacing the decisive memory cause an objective
  behavior change *above salience*?) — a necessary-condition proof + regression guard for the §13.10
  prohibitive-memory path, **not** the thesis.
- **Hard gates (non-negotiable):** a held-out **negative-control set** (brain has no relevant memory →
  brain-on must NOT win; clear *this* null, not zero); a **generic-salience arm**; a **content-ablation
  arm** (strip only the relevant memory); memories must be **episodes/why** (reason-from, not
  warning-shaped → no leakage); **programmatic blind judging** (a code detector, not an LLM grading
  prose — firewall); **pre-registration**; **per-case** stats + anytime-valid CI / Beta-Binomial.
- **Better primitive (build toward):** **within-task decision-point ablation** — at real dogfood
  decision points, two next-actions (brain-context vs ablated/decoy), score the action by an external
  check. Per-*decision* N (biggest power lever), uncurated (recovers external validity). M-1a is its
  curated special case. **Task-replay** (real tasks, tests-as-judge) is closer to true M-1 but
  thin/expensive here. **Per-recall IPS** is the right long-run ATE estimator but **blocked on R-7**
  (`propensity=1.0` → no common support → IPS *undefined*); build the propensity **logging** now, defer
  the estimator (needs stochastic serving + volume = likely multi-user). Soft signals = guardrails only.
- **The real new construction (shared):** an **actuator-in-the-loop harness** (consumes the served set →
  emits an action) + programmatic detectors. `harness.compare`/`NullRetriever` already give the
  retrieval on/off switch. **Build order:** (a) actuator harness + detectors; (b) M-1a with all guards,
  pre-registered; (c) graduate to decision-point ablation; (d) R-7 propensity logging in parallel.
- **Spec:** §16; planning.md; path-to-real-data.md; `eval/harness.py`, `instrumentation/logging_retriever.py`.

### M-2. `plan`-tool brief-quality eval (L1.5 gotcha-cases) — `built` · ✅ *(2026-06-17)*
- **What:** A curated "gotcha-case" set: does the brief surface the relevant constraint/gotcha/finding
  when one is known to apply? Built as `plan-brief-eval` (CLI) + `eval.plan_brief` (harness): cases =
  (target, expect_memory_id|expect_text); reports gather recall + misses. **Anti-circularity baked in**
  (cases must be pre-existing, human-judged content — else it only measures a link round-trip).
- **Spec:** `eval/plan_brief.py`, `cli/plan_brief_eval.py`; starter set `docs/eval/plan_brief_cases.json`.

### M-4. Rung-validation eval — utility-join ablation — `built` · ✅ *(2026-06-17)* *(the L1 for the rungs)*
- **What:** `rung-eval` — validates the retrieval rungs by the **right** metric: re-run each past
  recall's cue through `brain-off/+usage/+structrel/+central/+full` and score recall@k/MRR/hit of the
  memory **actually used** (label from the usage/attribution logs, joined by `eval.cases_from_usage`).
  `--split` does a leak-free temporal split (weights from older recalls, test on newer *labeled* ones).
  Reuses `serve_config` so a config-rich brain (sample-project SCIP/corpora/`data_dir`) builds as the live
  tool does. **This is what M-1 needs at L1** — the surface metric (`probe-eval`) can't judge re-rankers.
- **Verdict delivered:** see L-R1 (usage: real but recall tradeoff) and L-R2 (centrality: clean winner;
  structrel: dropped). **Open follow-on:** more `record_usage`/attribution volume (L-1) for a larger,
  multi-brain test set; the explicit `record_usage` signal is dead — attribution carries it (D-4).
- **Spec:** `eval/benchmark.py` (`cases_from_usage`), `cli/rung_eval.py`, `cli/rung_arms.py`.

### M-3. Benchmark freeze-vs-refresh (Goodhart) — `idea` · **P3**
- **What:** Thin live signal → over-reliance on the frozen benchmark. Manage the freeze/refresh
  tension; the thesis "more use → more useful" slope itself may be confounded.
- **Spec:** §16; outcome-learned-retrieval.md open questions.

---

## Track D — Brain-1 data quality

### D-1. Episode vs curated embedding comparability — `partial` · **P2**
- **What:** Episodes embed `content` only; curated embed a composed `kind+text+why+footprint` string.
  Cross-kind cosine comparability is unverified.
- **Spec:** `experiential/ingest.py:56` vs `cli/remember.py:258`.

### D-2. SessionStamping fragility — `partial` · **P3**
- **What:** `SessionStampingSource` assumes one active serve session per repo; a commit after serve
  exit (stale `current.json`) mis-attributes. Add a freshness bound on `last_recall_at`.
- **Spec:** `experiential/sync.py:157-200`.

### D-3. Config cleanup — `idea` · **P3**
- **What:** `dim` defaults to 128 in CLI but BGE is 768 (encoder overrides at call sites) — misleading
  dead config for the BGE path. Tidy.
- **Spec:** `cli/{remember,dogfood}.py`.

### D-4. The `used:true` rate — `ANSWERED` · ✅ *(2026-06-17)*
- **Answered (not a write-path bug):** explicit `record_usage` (the SECONDARY *citation* signal) is
  genuinely low — sample project 0 / this repo ~6 `used:true` — because actuators rarely cite recalled
  *content* strongly. The PRIMARY signal is footprint **attribution** (`usage_attributed.jsonl`: 261
  sample project / 83 this repo `used:true`), which is durable and works — so the rungs + verdict are fed.
- **The live signal worth pursuing instead → see R-9:** 52 of 75 memories surfaced ≥2× are reliably
  *ignored* (only 2 reliably-used). That's a **recall-precision** question, not a capture bug.
- **Spec:** `experiential/fate.reuse_by_memory`, `cli/attribute.py`.

---

## Track S — Security & content-trust  *(tracked, deliberately lower priority)*

> §17 [`security.md`](deep-dives/security.md). **Stance: scoped, not "more is better."** Zero code
> today, correctly — solo operator, own repos. Build order is deterministic (no learned model) and
> **gated**: this workstream does *not* jump ahead of credibility step B unless the threat surface
> changes (ingesting un-authored corpus, or exposing the brain beyond the local operator).

### S-1. Provenance tagging at capture — `design` · **P2/P3** *(do first within S)*
- **What:** Tag every memory/node `trust: operator | derived | third-party` at capture, keyed on the
  producer. Near-free, and the prerequisite for everything else in S.
- **Why:** Addresses T1 (injection) + T3 (poisoning) groundwork; cheap to add now so later fencing has
  the signal.
- **Spec:** §17.x; ties to the producer registry (producer → trust level).

### S-2. Recall-path content fencing — `design` · **P3**
- **What:** Fence ingested third-party content at the single choke point (`gateway/payload.py`) so
  instruction-shaped text in code/docstrings/commit messages can't hijack the actuator (T1).
- **Gate:** needs S-1 (provenance).

### S-3. Secret redaction at ingest — `design` · **P3**
- **What:** Redact API keys/tokens/`.env`/hostnames before they enter the vector index + Neo4j (T2 —
  far harder to expunge once embedded).

### S-4. Poison-resistance in credibility — `design` · **P3**
- **What:** Compose adversarial-content resistance into the credibility layer (supersession/credibility
  handle *drift*, not *planted* content).
- **Gate:** **gated behind credibility step C** (L-5).

**Explicit non-goals (don't build):** multi-tenant RLS / per-user auth (the `(tenant_id, repo_id)`
scope is *namespacing*, not a security boundary), key rotation, network hardening beyond localhost.

---

## Strategic open decisions (not tasks — choices to make deliberately)

- **Multi-user** — the recurring unlock for the *learned* layers (single-user volume may make them
  un-validatable), but it trades data-starvation for a transfer/contamination problem ("useful in
  repo A" predictive in repo B?) and turns scope into a real security boundary (Track S). A
  first-class decision, not a footnote. *Spec: §16 / outcome-learned-retrieval.md.*
- **Which track leads next** — Track L's *unblocking* work (L-1→L-3) is highest priority, but Track-C
  work feeds L with volume. Likely answer: **do both in parallel, with capability work done *through*
  the brain** so it generates the learning data. Decide the mix explicitly.
- **Is the thesis metric cleanly measurable at all** (M-1/M-3) — accept it may only be an
  interval-delta + soft-signal story at single-user scale.

