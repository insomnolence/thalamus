# Thalamus — Outstanding Work & Ranked Roadmap

*The single backlog of everything still to do, so nothing gets lost. Complements — does not
replace — [`design-notes.md`](design-notes.md) §16 (the canonical capability roadmap), the
deep-dives (per-area specs), and [`STATUS.md`](STATUS.md) (what's in-flight right now). This file
is the **ranked, actionable superset**: every item carries a status, what it does, why, the
concrete steps, its gate/dependency, and where it's spec'd.*

*Last updated 2026-06-15.*

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
  data quality**, **Track S — Security** (§17 [`security.md`](deep-dives/security.md), tracked but
  deliberately lower).

**Two cross-cutting truths that drive the ranking:**

1. **Building *is* using.** The learning signal (Tier-1 usage + Tier-2 outcome) is a *byproduct of
   real dev work done through the brain*, not of standalone queries. So Track-C work, done *through*
   the brain, **feeds** Track L with the dogfood volume + negatives it's starved of. They are not
   competing — capability work is also data generation.
2. **Credibility C is gated on B is gated on data.** The learned re-ranking payoff (L-C) cannot be
   trusted until the proxy↔truth monitor can discriminate (L-B), which needs *negatives* in the data
   (L-A loop / friction capture). Shipping a feedback layer you can't measure is the exact Polynoica
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
| 1 | **Usage-weighted retrieval rung** (L-R1) | L | The active learning build: lift recalled-and-used + cross-session-reused memories. Feedable *without* code outcomes (usage accrues every session); surfacing the right/current stuff over stale is the token-saving win. Supersession-demotion + recency already ship. |
| 2 | **`plan` / impact tool** (C-3) | C | The headline capability — fuse current/used memories + Brain-2 structure into one tight brief. Works for any domain; doesn't need the outcome loop. |
| 3 | **External-analysis findings producer** (C-1) | C | First drop-in on the producer seam; enriches Brain-2 (more for recall + the plan brief to draw on). |
| 4 | **Structural-centrality weighting** (L-R2) | L | Lift memories well-connected to Brain-2 — the substrate the plan tool fuses. |
| — | **PARKED:** the outcome loop (churn / monitor / counterfactual) | L | Built + gated but dormant — no captured code outcomes in this workflow. Kept for if instrumented coding resumes. |

After these: the re-ranker (credibility C, gated on 1+2+3+4), multi-language ingestion, retrieval
hardening, then the security workstream. Full detail below.

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
> **never** the model grading its own memory prose (Polynoica). Using top-ranked memory *content* to
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

### L-1. Fix the Tier-2 join starvation — `parked` · ~~P0~~
- **What:** Recalls aren't reliably joining to outcomes. 61/110 retrieval events have
  `session_id=null`; usage signals are dominated by `used:false`; `record_usage` is under-called.
- **Why:** The proxy↔truth verdict joins per-session Tier-1 (utility) to Tier-2 (fate/outcome). No
  keys → no join → `n_units≈1` forever. This is the floor under all of Track L.
- **Do:** (a) Audit where `session_id` is dropped (recall path → retrieval-event log; the
  time-window attribution join in `attribute.py` is the resilient fallback — confirm it covers the
  HTTP multi-agent case). (b) Tighten the dogfood `record_usage` discipline (CLAUDE.md already
  mandates it; verify it's actually firing). (c) Confirm episodes are persisted in **Neo4j** in the
  live instance, not just in-memory (the on-disk artifacts are logs + a curated backup only).
- **Gate:** none — do first.
- **Spec:** dreaming.md (verdict), §13.11 logging contract; code: `cli/{verdict,attribute}.py`,
  `gateway/server.py`, `experiential/sync.py` (SessionStampingSource).

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
  fate-trained ranker measured by a fate metric is circular — the quiet Polynoica trap. Doing C before
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
- **Next (follow-ups):** mid-serve refresh of the weights (a dreaming refresh, like the superseded
  frontier); `harness.compare` L1 eval (does it surface the reliably-used core higher?); tune `weight`.

### L-R2. Structural-centrality weighting + Brain-2 correlation — `idea` · **P2**
- **What:** Lift memories well-connected to the structural graph / Brain-2 knowledge; the substrate the
  `plan` tool (C-3) fuses. The "connect architecture memories with where the function goes" step.
- **Gate:** soft-depends on L-R1 + the cross-link coverage (C-2).

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

### C-1. External-analysis findings producer — `design` · **P1**
- **What:** A `FindingsIngestor` (kind `findings`): an external tool (static/security/program
  analysis, e.g. code-scalpel) writes a normalized JSON via `regen_command`; the ingestor reads it →
  `finding` nodes anchored to code. The *first* thing built on the new producer seam.
- **Why:** High leverage, clean drop-in (`register_producer`), and findings-through-the-brain are
  exactly the kind of cross-cutting context the `plan` tool needs — and generate dogfood volume.
- **Do:** Define the normalized findings schema; write `FindingsIngestor` (anchor findings to
  `SourceAnchor` → code nodes); register the producer; wire `regen_command` gating (already generic).
  **Depends on C-2** for the findings to actually fuse into recall via cross-links.
- **Gate:** soft-depends on C-2 for cross-linking.
- **Spec:** §16 step 4; structural-hemisphere.md (deferred producers list).

### C-2. Anchor-based cross-linking of non-code nodes to code — `design` · **P1**
- **What:** Today non-code corpora (docs, text, findings) surface only via *direct* retrieval,
  correctly tagged by corpus. To make a finding/doc *fuse* into a code node's context (and the `plan`
  brief), link non-code nodes to code by `SourceAnchor`. Only then does the `corpus="code"` default
  in the gateway cross-link path (`payload.py from_node`, gateway `_structural_for`) matter.
- **Why:** Prerequisite for findings/docs to show up as "what the brain knows about this code," which
  is the core of the `plan` tool.
- **Do:** Add anchor→code-node resolution in the cross-link layer; generalize the corpus tag in the
  cross-link path.
- **Gate:** unblocks C-1 fusion and C-3.
- **Spec:** §13.19; structural-hemisphere.md (deferred).

### C-3. `plan` / impact tool (v1) — `design` · **P1/P2**
- **What:** Given a target, resolve the integration point → compute structural blast radius
  (forward/reverse reachability, bounded hops) → gather attached decisions/gotchas/docs/findings →
  return one fused brief. Deterministic core.
- **Why:** The vision's headline capability — "give the actuator the forest."
- **Do:** Build steps 1–4 from planning.md (symbol resolution via hybrid retrieval + semantic
  fallback; deterministic graph traversal; cross-link + curated gather; structured payload assembly
  with staleness flags + superseded-but-included lineage). Defer NL synthesis (open question).
- **Gate:** needs hybrid retrieval ✅ + Brain-2 coverage good enough + C-2 (cross-link coverage). The
  eval (L3 "fewer cross-cutting mistakes") is hard — start with an L1.5 "gotcha-case" set (see M-2).
- **Spec:** [`planning.md`](deep-dives/planning.md) (full design + open questions); §16 step 5.

### C-4. Multi-language structural ingestion (tree-sitter producer) — `design` · **P2**
- **What:** A lightweight syntactic (tree-sitter or equivalent) ingestor as a producer, for
  languages whose SCIP indexer is unavailable/too heavy.
- **Why:** Broadens Brain-2 coverage; dollhouse is TypeScript (765 files) and today only SCIP covers
  it — a tree-sitter producer is a lighter path. Language becomes a plugin axis.
- **Do:** `register_producer` a tree-sitter ingestor; map its nodes/edges to the open schema.
- **Gate:** none (drop-in on the seam); prioritize behind findings + plan.
- **Spec:** §16 step 3; structural-hemisphere.md (deferred).

### C-5. `research` tool — `idea` · **P3**
- **What:** Deeper cross-hemisphere "what do we know about X" synthesis. To be designed.
- **Spec:** §16 step 6.

### C-6. Producer entry-point / plugin auto-discovery — `idea` · **P3**
- **What:** External plugins register producers via entry points (the in-process registry is the v1
  seam).
- **Spec:** structural-hemisphere.md (deferred).

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

### R-7. Off-policy propensity (IPS) — `design` · **P3**
- **What:** `propensity=1.0` is a placeholder; real IPS/off-policy estimation needs stochastic rungs.
  Unblocks honest evaluation of learned rungs (L-6).
- **Spec:** logging_retriever.py:75.

### R-8. Misc structural coverage — `partial` · **P3**
- Router protocol has no impl (intent routing deferred). · Nested defs not indexed (AST v0). ·
  Cross-module inherit/import is a Python-AST heuristic (SCIP covers it). · `scip-python` upgrade for
  precise Python calls. · Bent-geometry retrieval (§13.4) gated on confirming the recall-miss
  hypothesis from logs.

---

## Track M — Measurement / eval

### M-1. L3 brain-on vs brain-off ablation — `design` · **P2**
- **What:** The only metric that validates the thesis: does a brain-informed actuator make fewer
  cross-cutting mistakes on real tasks? Hardest to measure; confounded by easier-tasks/changed-user.
- **Do:** Design the ablation + fallback soft signals (time-to-resolution, dead-ends, L3 deltas at
  intervals).
- **Spec:** §16; planning.md; path-to-real-data.md.

### M-2. `plan`-tool brief-quality eval (L1.5 gotcha-cases) — `design` · **P2**
- **What:** Before L3 is feasible, a curated "gotcha-case" set: does the brief contain the relevant
  constraint/gotcha when one is known to apply?
- **Gate:** pairs with C-3.
- **Spec:** planning.md open questions.

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

### D-4. Investigate the `used:true` rate — `partial` · **P2**
- **What:** Usage is dominated by `used:false`. Confirm whether `record_usage` is genuinely
  under-called or a write-path bug drops signals (trace `record_outcome` + the bounded pending-payload
  FIFO eviction). Overlaps L-1.
- **Spec:** `gateway/server.py:94-150`.

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

---

## Commit / housekeeping (immediate, mechanical)

- Universal-ingestion code: **committed** (`449df4f`); `.mcp.json` untracked (`4c05df1`).
- Uncommitted doc updates awaiting your review: `README.md`, `design-notes.md`,
  `structural-hemisphere.md`, `planning.md`, `security.md`, and **this file**.
- `scripts/` (codex.sh, serve-http.sh) intentionally left untracked for now.
