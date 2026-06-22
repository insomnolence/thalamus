# Deep dive — Outcome-learned retrieval (§13)

*Part of [Project Thalamus design notes](../design-notes.md), worked through 2026-05-24. Section
numbers `§13.x` are used globally across the design notes; cross-references here to §1–§12, §14, §15
point back to [`design-notes.md`](../design-notes.md).*

> **Re-aimed 2026-06-15; rung verdict added 2026-06-17; Architecture B, R-9 diagnosis, R-7 build,
> and M-1 panel findings added 2026-06-17.** This document was written around the **code-outcome**
> direction (did committed work survive / tests pass). The credibility/learning track's **target
> signal has moved** to **relevance credibility** — which memories are *current / used / important /
> well-connected* — because outcome capture does not accrue in the primary workflow, whereas usage +
> supersession + recency + structural centrality accrue every session. The **outcome loop** (churn /
> `session_fate` / proxy↔truth-on-commits) is **PARKED** — built, gated, dormant; kept for if
> instrumented coding ever resumes. What carries over unchanged: the **firewall** (§13.7 —
> external/behavioral facts only, never the model grading its own prose), the **modularity seams**,
> and the **research toolkit** (SNIPS / anytime-valid CIs / ranking), which re-target cleanly onto a
> usage-based ranker. Authoritative plan for the current direction: [`learning-loop.md`](learning-loop.md).
> ROADMAP.md Track L has the ranked build order.
>
> **Rung verdict (de-leaked utility-join, 2026-06-17 — on a code-rich sample-project brain and the
> this-repo brain):**
> - **L-R2 global — `StructuralCentralityRetriever` (structural-centrality): the clean winner.**
>   Lifts recall AND MRR on both brains, no tradeoff; graph topology only, non-circular.
>   Applied **outermost** (leads the live ranking). This is the rung whose signal is independent of
>   usage labels and survives the de-leak cleanly.
> - **L-R1 — `UsageWeightedRetriever` (usage): real signal, but an intrinsic recall@k tradeoff.**
>   Past usage predicts future use and the lift survives de-leaking, but it over-promotes
>   used-but-not-yet-popular memories at the cost of recall@k. Big win on the process-heavy brain
>   (this repo); a recall wash on a code-rich sample project. RRF weight 0.5≈1.0 — not tunable away. Kept ON
>   (applied **inner**, so centrality leads); disablable via `usage_weighting=False` where recall
>   matters more than the top hit.
> - **L-R2 query-local — `StructuralRelevanceRetriever` (structural-relevance): earns ~nothing on
>   both brains → DROPPED from the live chain.** `structural_relevance=False` is the default;
>   the rung is kept behind the flag as a removable §14 layer for a future rework, but it is not
>   wired into the live ranking chain.
>
> **So the three rungs shipped, but the live default is: centrality (leads) + usage (inner, with
> recall@k tradeoff); structrel off.**
>
> **Scope of the verdict (R-9 diagnosis, 2026-06-17):** The verdict is scoped to
> *footprint-labelled* memories evaluated on *code-touching* sessions. The 52-of-75 "reliably
> ignored" finding is ~87% a measurement artifact: footprint-empty curated memories (firewall,
> vision, discipline) receive `used=False` by construction; the citation signal `|mem ∩ out| / |mem|
> ≥ 0.5` penalizes long memories (only ~1.1% of live citation signals clear 0.5); plus stale
> attribution. **Do not demote the "ignored" set** — it is dominated by architectural/orientation
> memories, not junk (a Goodhart trap). The deeper open gap: there is no firewall-clean behavioral
> credit signal for *conceptual* recall (an orientation memory that silently kept the actuator on
> track but was never cited). The only clean path is an explicit `record_usage` declaration (the
> actuator naming the memory used, independent of token overlap). The verdict stands for its scoped
> domain; it is silent on conceptual and orientation recall.
>
> **Usage-signal reality:** explicit `record_usage` calls are effectively dead (this repo ~6 true,
> the sample project 0). The **time-window attribution log** (`usage_attributed.jsonl`, via `attribute.py`)
> carries the real usage signal — sample project 261 attributed, this repo 83. Attributed usage is
> backward-looking: a recall is labeled "used" only once later commits touch its footprint, so the
> newest recalls are unlabeled. Where the docs lean on `record_usage` as the usage signal, read
> "attribution" instead.
>
> **Architecture B (Track I-3, 2026-06-17):** The usage rung previously recomputed weights from raw
> JSONL log files at each maintenance tick. Now a `BehavioralConsolidationPass`
> (`dreaming/behavioral_consolidation.py`) folds the log write-ahead buffer into a durable
> `BehavioralStore` backed by `Neo4jBehavioralStore` (`experiential/neo4j_behavioral.py`) — one
> MERGE'd node per `(memory_id, session_id)` used-pair, additive label `M_behavioral_use`,
> idempotent. `UsageWeightedRetriever` now calls `behavioral_store.usage_weights()` **from the
> brain**. Because the store is a set-union, re-folding any subset of the raw logs never
> double-counts and never loses signal — the raw JSONL files demote to a disposable write-ahead
> buffer with no cursor needed for correctness. The brain is now the system of record for its own
> behavioral history. Shadow-validated before cutover (44 memories, exact match). See §13.8
> (signal taxonomy) for where behavioral credit sits in the firewall.
>
> **Propensity logging (R-7, 2026-06-17):** `propensity=1.0` (deterministic top-k) made off-policy
> estimation undefined (no common support). **Built:** `ExploringRetriever`
> (`retrieval/exploring.py`) — a two-policy mixture (with prob 1−ε deterministic top-k; with prob ε
> a uniform random k-subset of the top-pool) with an exact per-item marginal propensity stamped into
> each shown item's `features["propensity"]`; `LoggingRetriever` now logs that. Off by default
> (ε=0, live recall unchanged). The IPS/SNIPS estimator is **deferred** — this builds the logging
> substrate (the irreversible-if-deferred half). See §13.9 and §13.11.
>
> **M-1 (brain-on/off ablation) — design examined (3-expert panel, 2026-06-17):** The naive
> "gotcha-avoidance" ablation is circular as proposed (see §13.20). Reframed as **M-1a — a
> conversion/delivery probe** (necessary-condition proof for the §13.10 prohibitive-memory path,
> not the thesis itself). Hard gates: negative-control set, generic-salience arm, content-ablation
> arm, episodes/why memories (not warning-shaped), programmatic blind judging, pre-registration.
> Pre-registration protocol: `docs/eval/m1a_preregistration.md`. See §13.20 for full treatment.
>
> **Negative result to preserve:** `probe-eval --rungs` ablates rungs by the SURFACE metric
> (top-1 cosine). That metric **saturates** and cannot discriminate re-rankers — it is not useful for
> judging rung quality. `rung-eval` (utility-join: does the rung rank the *actually-used* memory
> higher, scored by recall@k / MRR / hit@k against the attribution-labeled set, with `--split` for a
> leak-free temporal split) is the right instrument for re-rankers.

---

## 13. Overview

First frontier thread taken to depth. Path: the disease → why this arena can beat it → the
mechanism → exploration → hindsight relabeling → logging/eval → honest limits → rejected options.

### 13.1 Stance

"Outcome-learned retrieval" is **not** a synonym for "train a neural retriever." Near-term —
possibly for years at single-user scale — it cashes out as **outcome-weighted ranking with
calibrated exploration** (an online, non-parametric usefulness estimate). The trained retrieval
*geometry* (§13.4) is the high-ceiling, gated bet on top. The per-memory statistic is the **floor
and the measuring instrument, not the answer**: it provably cannot generalize to unseen memories
(cold start), model context/interaction effects, or fix a mis-organized space. Governing axis:
**measured vs. unmeasured, not easy vs. hard** — build the boring baseline as the stick, then
reach for the frontier and measure it against the stick. Symmetric trap: novelty-for-its-own-sake
(difficulty as a proxy for quality) is what sank our predecessor project — reach *with* the instrument, never blind.

### 13.2 The disease: the recall ceiling

A reranker, a usefulness statistic, or a bandit over fixed features can only **reorder what the
frozen embedding already surfaced.** If the needed memory isn't in the cosine top-k pool, nothing
downstream recovers it. Every conventional layer (LTR, cross-encoder, LLM reranker) reranks a pool
generated by a space organized around *aboutness*; the operationally-essential-but-topically-distant
memory never enters the pool. **Fixing this requires changing the geometry, not reweighting it** —
the line conventional approaches don't cross.

**Caveat — this is a hypothesis, not an established fact.** That misses are predominantly
*recall*-misses (stored-but-unretrieved) rather than *capture*-misses (never stored) or pure ranking
must be **confirmed from step-1 logs before §13.4 is worth building** — the logs distinguish them. If
capture-gaps dominate, geometry-bending attacks the wrong problem.

### 13.3 Why this arena is winnable when others aren't

Code agents get **deterministic, near-objective ground-truth outcomes** — `pytest`, `mypy`,
commit-vs-revert — that recsys/search never have (they get noisy clicks). Outcome-driven retrieval
everywhere is bottlenecked on reward quality; this arena hands you an unusually clean reward. That
is the structural reason Thalamus could *supersede* conventional retrieval here, not merely match it.

### 13.4 The frontier move: bend the geometry with outcomes

- **Substrate (boring, proven):** dual-encoder dense retriever, **warm-started from BGE** (bend an
  already-good space; don't learn from scratch), contrastive loss.
- **Novelty (removable, measurable):** the **supervision source** — positive/negative pairs come
  from observed *outcomes*, not relevance labels. Pull *useful* memories toward queries, push
  *useless-when-retrieved* ones away, regardless of topical similarity. Switch off → frozen BGE.

### 13.5 Bandit + representation = one loop, not competitors

Different layers of one system:
- **Bandit** = decision + data-collection layer; with **per-item Tier-1 feedback it is a
  *semi-bandit***, which is what makes credit assignment tractable.
- **Representation learner** = candidate-generation layer.
- **The loop:** exploration surfaces distant memories → some prove useful (clean outcome) →
  training pairs → geometry pulls them closer → better pool → bandit exploits a better space.
  This *is* "more use → more useful," grounded in an external signal.

### 13.6 Exploration is load-bearing for *correctness*, not just data

If the candidate pool is generated by the same space being trained, the space only sees pairs it
already believes → self-reinforcement → a confident, self-consistent, possibly-wrong geometry
(our predecessor project's "it's learning!" illusion in respectable dense-retrieval clothes). **Exploration
injects pairs the space wouldn't pick; the external outcome adjudicates them.** Exploration is
therefore what keeps the loop non-self-referential — not merely a data-volume tactic.

### 13.7 Anti-self-validation invariants

- Every training label comes from a signal **independent of the optimized space** (deterministic
  overlap, frozen BGE, `pytest`, git, human) — never the learned geometry judging itself.
- Cosine-in-the-learned-space is the **optimization target**, not the reward. (Our predecessor project's
  fatal move was using cosine-in-its-own-space *as* the reward.)
- Removable + measurable: nested baselines (frozen top-k ⊂ +bandit reweighting ⊂ +bent geometry),
  each the stick for the next.
- **Model-interpretation firewall:** model-generated content (inferred whys §13.17, actuator-as-judge
  §13.9, dreaming abstractions §13.18) may be *retrieved* and *proposed*, but only **external** outcomes
  (tests, kept-vs-reverted, human) may *validate* or *reweight* it. Never let a model's interpretation
  become its own validation signal — that is the self-validation trap in slow motion.

### 13.8 Signal taxonomy

Layered by attribution-cleanliness vs. meaningfulness; **train on the clean-shallow layer, gate on
the meaningful-noisy layer.**

| Tier | Signals | Role |
|---|---|---|
| 0 — Exposure | in candidate set? rank? selection propensity? | bookkeeping for unbiased/off-policy learning; *unreconstructable later* |
| 1 — Usage | **content/symbol overlap (deterministic — primary)**, constraint-honored; citation (actuator self-report — *secondary, cooperation-dependent* per §13.11b) | **training target** (attributable, enough volume) |
| 2 — Task outcome | `pytest`/`mypy`, kept-vs-reverted, accept/modify/reject, later-contradicted | **gate/validator** (meaningful, confounded) — *not* the training target |
| 3 — Explicit | thumbs, user edits a memory | gold, too sparse to train on |

Every signal is an **external act**, never a distance in the retriever's space → self-reference
(FM1) impossible by construction.

**Tier-1 implementation note (Architecture B, 2026-06-17):** the Tier-1 behavioral usage signal
is now durable in the brain itself. `BehavioralConsolidationPass` folds the Tier-1 log WAL into
`Neo4jBehavioralStore` (`M_behavioral_use` nodes, one per `(memory_id, session_id)` used-pair,
idempotent set-union), and the usage rung reads weights directly from that store. The raw log
remains a write-ahead buffer. The Tier-0 propensity signal is now correctly stamped per item by
`ExploringRetriever` (was a hardcoded placeholder 1.0 prior to 2026-06-17).

**Known blind spot in Tier-1:** the footprint-attribution path (the primary `used` signal) is
structurally blind to conceptual/orientation memories that carry no code footprint. Such memories
receive `used=False` by construction, regardless of behavioral value. The citation signal
(`|mem ∩ out| / |mem| ≥ 0.5`) is also blind to them (low hit rate on long memories). This is not
a bug to fix with ranking; it is a measurement ceiling. The firewall-clean path to credit conceptual
recall is an explicit `record_usage` declaration from the actuator.

### 13.9 The exploration hierarchy

Conventional ε-greedy/Thompson "explore in what you show" is **unacceptable here**: no traffic to
amortize, each impression high-stakes (a bad exploratory memory can derail the dev *and* poison the
outcome signal), reward expensive to generate. Central move: **decouple "what we learn from" from
"what we show."** Sources ordered by (cost-to-user × bias):

| Layer | Source | Cost | Signal | Note |
|---|---|---|---|---|
| 0 | **Hindsight relabeling** (§13.10) | free | real, positives | centerpiece |
| 1 | Slack-slot exploration | near-free | real | tail slots already marginal; budget by stakes |
| 2 | Counterfactual from logs (IPS) | free | biased, eval-mostly | bounded to shown-item support |
| 3 | Actuator-as-judge | cheap | model-biased | prioritizes only; never ground truth; self-reference risk |
| 4 | Deliberate online exploration | expensive | real | rationed; its job is to **calibrate Layers 0–3** |

Plus a **directed active-query channel** (one-tap "useful?" on the ~1% highest value-of-information
cases — exploration in the *labeling* dimension). Keep novelty/under-observation driving *what to
explore* strictly separate from outcomes driving *usefulness* (saliency≠novelty, per §4).
**Dreaming/consolidation is the offline home for the expensive Layers 2–3.** Stakes estimation
prefers deterministic signals (file paths, dirty tree, tests green, mid-multi-step edit).

**Layer-2/4 substrate status (R-7, 2026-06-17):** the propensity-stamping prerequisite for Layers
2 and 4 is **built** (`retrieval/exploring.py` — `ExploringRetriever`, `explore_selection`). Prior
to this, every logged event had `propensity=1.0` (deterministic top-k), which makes IPS estimation
*undefined* (no common support), not merely noisy. The `ExploringRetriever` computes an exact
per-item marginal propensity for a two-policy mixture (deterministic top-k with prob 1−ε; uniform
random k-subset of the top-pool with prob ε), stamped into `features["propensity"]`. Off by default
(ε=0). The IPS/SNIPS estimator over accrued stochastic-serving volume is deferred — this builds
the logging substrate, the half that is irreversible if deferred.

### 13.10 Hindsight relabeling (the centerpiece)

**Invariant:** the solution is a *discovery-time* signal only; the **trained pair is (original
query, M)** — bridge query→memory *before* any solution exists. The solution never touches inference.

- **Trigger:** episode closes Tier-2-positive (passed + kept).
- **Additive-vs-prohibitive asymmetry:** additive memories ("use X") leave a positive trace in the
  diff; **prohibitive/constraint memories ("don't do Z") manifest as an *absence*** and are
  invisible to diff-overlap — yet are the most valuable, hardest-to-retrieve class.
- **Fix — mine the *trajectory*, not just the solution:** errors, dead-ends, reverts, failed test
  runs (all deterministic/external). **A memory matching an error the agent actually hit is a
  strong positive for a prohibitive memory**, recovering the coverage gap. Final diff → additive
  positives; trajectory failures → prohibitive positives.
- **Deterministic ladder for mining positives:** (a) symbol/identifier overlap [gold], (b)
  structural-graph k-hop overlap [via Brain 2; also the cross-hemisphere link, §9.3], (c) frozen-BGE
  similarity [weak, last resort].
- **Distillation-collapse trap:** if mining is BGE-dominated, the learned space just reproduces BGE
  and learns nothing. **The valuable positives are deterministically-connected but BGE-*distant*.**
- **The cross pattern (the core):** valuable **positives** = outcome-connected + query-BGE-distant
  (pull together); valuable **hard negatives** = query-BGE-close + outcome-unconnected + unused
  (push apart). Both bend the geometry off aboutness toward usefulness.
- **Constraint-mislabel danger:** "shown-but-no-overlap" mislabels honored-constraint memories as
  negatives → would suppress the most valuable class. Rescue via trajectory/constraint-honored
  checks; exclude prohibitive types from the naive negative pool.
- **Survivorship bias:** relabeling fires only on wins → also mine **failed** episodes for negatives
  (down-weighted; failures are noisier).
- **Causal validation (similarity ≠ would-have-helped):** confounds = (A) agent knew it anyway, (B)
  coincidental overlap. Stance:
  1. **Primary: gate the whole mechanism on net real outcomes** (relabeled vs. non-relabeled
     retriever, via interleaving/Layer-4). Don't validate each positive; make the confounds an
     empirical question the eval answers.
  2. Cheap pre-filter: **actuator ablation** (replay with/without the memory; drop those that don't
     change behavior — targets confound A). Model-biased; periodically checked against (3).
  3. Periodic ground truth: **Layer-4** deliberate retrieval on recurring queries.
  4. Heuristic: weight by **specificity/repo-locality** (generic knowledge the base model already
     has is likely redundant — confound A).
- **Tiered positive confidence:** gold (shown+cited+passed) > hindsight-strong (deterministic-distant,
  ablation-survived, specific) ≈ hindsight-trajectory (matches a real dead-end) > hindsight-weak
  (BGE-only/generic; low weight or excluded).
- **Staleness:** relabeled pairs decay; belief-revision supersession (§9.1) invalidates relabels on
  superseded memories.
- **Scope limit:** relabeling expands recall for what *proved* relevant; it cannot discover the
  unprecedented — that is Layer-4's job.

### 13.11 Logging contract (irreversible-if-deferred — must land in the step-1 foundation)

Two append-only logs. Decision-time features and propensities **cannot be reconstructed after the
fact**; omit them at step 1 and off-policy learning is permanently impossible.

**Propensity status (2026-06-17):** the per-item realized propensity is now correctly stamped by
`ExploringRetriever` (R-7 build). The old `1.0` placeholder is retired for sessions running with
ε>0. At ε=0 (the default) the logged propensity is still 1.0 — correct, but with no common
support for IPS; the estimator therefore stays deferred until stochastic sessions accrue. The
ranker-version field is also stamped, enabling free intervention-harvesting across reships.

**(a) Retrieval-event log.** Per retrieval event: event/session/timestamp; cue (raw prompt+focus,
embedding, intent label); **candidate set with each candidate's feature vector at decision time**;
**what was shown + ranks + propensities**; payload version; **stable `memory_id` that survives
consolidation/merging** (so outcome lineage isn't orphaned when dreaming merges memories). Joined
later by event_id: Tier-1/2/3 signals.

**(b) Episode-trajectory log** — the offline dataset for hindsight relabeling (§13.10); it *is* the
HER tape. **Design constraint that dominates everything:** Thalamus is a gateway; the actuator is
external and runs its own loop, so much of the trajectory is invisible unless observed *out-of-band*.
Lean on **environmental, actuator-agnostic observation** (file watcher + git hooks + a test-runner
hook) as the spine; treat actuator self-report as graceful enrichment that degrades to nothing when
absent — never build on it, or you forfeit the "works with any off-the-shelf editor" property.
Capture **salient transitions, not the raw stream** (deterministic salience per §4 — a test going
fail→pass is objectively salient):

| Signal | Mechanism | Det.? | Value |
|---|---|---|---|
| cue + intent; memories shown | gateway (self-generated) | yes | episode anchor; links to (a) |
| edit deltas; **edit-then-revert** | file watcher | yes | dead-ends |
| commit / reset / revert / stash | git hooks / reflog | yes | terminal outcome; dead-ends |
| **test/type/lint run + error payload** | **test hook (e.g. pytest plugin)** | yes | **prohibitive-memory signal** |
| **fail→pass transition diff** | test hook + watcher | yes | "what was actually needed" |
| agent searches/reads | gateway *iff* routed via Brain 2 | partial | unmet-need / info-seeking |
| human-vs-agent authorship | — | no | open (human fix of agent work = strong negative) |

**The one non-optional piece:** cue + final-diff + commit (gateway + git, near-free) yields only
*additive* relabeling; the **test-runner hook (failures + fail→pass) is the critical unlock** for the
*prohibitive*-memory half of §13.10. If you build one thing beyond git+watcher, build that.
Python-first stack: **pytest plugin + git hooks + file watcher** — all deterministic, none requiring
actuator cooperation; other languages need more test adapters (deferrable). **Open edges:** episode
segmentation (→ §12) and human-vs-agent attribution.

### 13.12 Eval / gating (pre-committed before the learning layer)

- **Primary (trainable):** `utility@k` = fraction of shown memories earning a Tier-1 signal.
- **Geometry-specific:** recall of **useful-but-BGE-distant** memories (isolates what only bending
  the space can do).
- **Gate (truth):** session-level Tier-2 lift (test-pass / keep-rate), via **interleaving**
  (team-draft) for low-traffic online A/B; **IPS** for offline pre-screening.
- **Critical safeguard — proxy↔truth divergence monitor:** if `utility@k` climbs while Tier-2 stays
  flat/drops → reward-hacking → kill the layer. (Needs enough Tier-2 volume to run; if starved,
  *don't deploy a learned layer* — flying blind is the failure.)
- **Exit criterion:** rung N beats rung N−1 on primary by a preset margin, proxy↔truth correlation
  intact, within a fixed window — or cut.

### 13.13 Honest open problems / risks

- **Data volume (the likely silent killer):** a single dev produces ≈ hundreds of clean outcomes/month;
  learned rankers want thousands. The cheap exploration layers (esp. hindsight) are what make
  single-user viable at all. **Validating exploration's long-term payoff may be impossible at
  single-user scale** → the exploration policy is probably what drags the **multi-user question from
  "later" to "sooner."** Multi-user trades data-starvation for a **transfer/contamination** problem
  (is "useful in repo A" predictive in repo B?).
- **Credit assignment is bounded, not solved** (gated by §13.12, not proven per-instance).
- Hindsight residue: survivorship + constraint-coverage + distillation-collapse (mitigated, §13.10).

### 13.14 Considered and rejected (not defaults)

- **Generative retrieval / DSI** (model emits memory IDs): re-indexing new docs needs retraining; a
  memory store **grows constantly** → near-disqualifying. Rejected on fit, not fashion.
- **Titans / test-time neural memory:** substrate novelty (parked by project policy) and a recall
  *substrate*, not a system-architecture fix.
- **LLM-as-reranker:** strong and increasingly conventional, but reranks a pool → doesn't fix recall
  (§13.2). Keep as a strong **baseline component**, not the frontier bet.

### 13.15 Build-order implication (refines §10)

Step 1 must already include the §13.11 logging contract (incl. trajectory capture) and the §13.12
metrics. Step 2's "usage/outcome-weighted retrieval" = the §13.5 semi-bandit + §13.10 hindsight
relabeling on the frozen-BGE baseline. The bent geometry (§13.4) is a §10.4 frontier layer, gated
against the step-2 baseline.

### 13.16 Episode segmentation (refines §12's "what is an episode")

**Doubly load-bearing:** an episode is both the training-pair unit for relabeling (cue→query,
outcome→trigger, trajectory→failure structure) *and* the storage atom of Brain 1. Mis-cutting it is
a **silent poison** — no error, just subtly mis-attributed pairs (wrong cue glued to wrong solution).

**Reframe:** there is **no ground-truth segmentation** — nesting/granularity are genuinely ambiguous;
episodes are a *modeling choice, not a fact to discover.* Goal isn't "find the true episodes" but
"pick robust-enough boundaries and make relabeling tolerant of error."

**The key move — segmentation is a derived *view*, not a live decision.** Only raw event capture
(§13.11b) is irreversible-if-lost; the *grouping* into episodes can be recomputed anytime from the
raw log. So **capture continuously with coarse deterministic boundaries live; re-segment offline in
dreaming as methods improve.** Segmentation is therefore *not* in the irreversible bucket — a big
stakes-reducer, and it gives dreaming a second job. (Mirrors §13.11's capture-raw-derive-later ethos.)

**The cue-vs-outcome tension + the menu (cheap → sophisticated):**
- **S0 — request-bounded** (default): one top-level request → next/idle. Anchored on the **cue**
  (what relabeling pairs against). Blind to interleaving; bundles multiple tasks per request.
- **S1 — commit-bounded:** work between commits; the commit *is* the Tier-2 outcome. But commits ≠
  tasks; abandoned work has no boundary. (S0 gives the query side, S1 the outcome side — join them.)
- **S2 — cue↔outcome join via structural footprint** (architecture-specific trick): disentangle
  interleaved work by *which code nodes each touched* (Brain 2 gives the symbol→node map), i.e.
  change-point detection on **footprint discontinuity**, robust to temporal interleaving. Fails when
  two interleaved tasks touch the *same* files → fall back to request/temporal bounds.
- **S3 — LLM semantic segmentation:** handles nesting/semantics, but expensive, non-deterministic,
  a learned judgment (against §4), mildly circular. **Defer; run in the dreaming pass, never live.**

**Robustness to error:** footprint-attribution beats clock-proximity; segmentation confidence
propagates into the §13.10 tiered-confidence scheme (clean single-request/single-commit/disjoint-
footprint → high-confidence pairs; ambiguous → down-weighted). The §13.12 net-outcome gate is the
ultimate but *slow, aggregate* arbiter — because the poison is silent, add a cheap
**segmentation-quality monitor** (per-episode footprint-coherence; periodic human spot-checks).

**The "why" residue (Brain-1-defining):** an episode's *span* is deterministic; its **why is
interpretive.** Provenance tiers: explicit-in-cue (best, often absent) > inferred-in-dreaming (mark
as model interpretation, lower-confidence) > explicit-capture-at-close (high quality, costs
attention/cooperation). This is where Brain 1's premise ("what we did *and why*") meets the limit of
deterministic capture — tag each why with its provenance.

**Parked radical alternative:** don't materialize episodes at all — keep them soft/overlapping spans
computed on demand, relabel against spans not hard partitions. Logical endpoint of "segmentation is a
view"; sidesteps no-ground-truth entirely. Elegant but premature — adopt only if hard-partition
relabeling demonstrably fails.

### 13.17 Capturing the "why" (advances §9.1, §12)

**Different in kind.** Everything else captured is *observable* (diffs, tests, reverts, citations);
the why is **interpretive, belief-like, not observable out-of-band.** It's what makes Brain 1 a brain
not a log — next time you apply the *reasoning*, not replay the action. So it breaks the
deterministic-capture pattern, and that discontinuity must be faced, not papered over.

**Why it's hard:** mostly tacit/unstated; only *ask* or *infer* (both lossy); stated whys are
**post-hoc narratives** prone to confabulation; it has structure a one-liner destroys — goal,
decision, **alternatives considered + why rejected** (the crown jewels), constraints/assumptions,
tradeoff; and it's a *belief* with a truth-value and a lifespan.

**Key insight — the trajectory's failure structure is the why's deterministic skeleton.** "Tried W,
hit error E, did Y" (from §13.11b dead-ends) is *evidenced* rejection. So **trajectory-grounded
inference beats after-the-fact asking** (which confabulates). And **separate computable whys**
("async because called from an async context", "signature load-bearing — N callers") — those are §4
gotchas *computed on demand from the graph*, never stored as fragile narrative — from genuine
rationale whys, which alone need capturing.

**Keep it honest:** tag every why-component **evidenced** (trajectory/structural) vs **asserted**
(narrative); confidence parallels §13.10. An *unmarked inferred why is the brain telling itself a
story about its own past* — a historical-narrative cousin of the self-validation trap.

**The synthesis — why = belief, not fact.** Split Brain 1: **immutable episode history** (append-only
facts) + **mutable belief layer** (the whys: time-stamped, assumption-bearing). Belief revision
(§9.1) lives in the why layer — never rewrite history, supersede *beliefs*. A why carries the
**assumptions it held under**; assumption-violation (e.g. single-user → multi-user) flags the
justified decision for review. Outcomes also validate whys: credibility = **longevity-without-
supersession + downstream success** (the external-outcome discipline, applied to rationale).

**Leaning stance — the why as a derived *view*, not a stored narrative** (parallel to §13.16): store
the **evidenced primitives** (goal from cue, rejected-alternatives from trajectory dead-ends,
constraints from the graph) + an *optional asserted narrative tagged low-trust*; **reconstruct the
presented why on demand in dreaming** from *current* evidence + context, so it's never a stale story.
A real bet — it leans hard on dreaming-time reconstruction.

**Hard floor:** tacit reasoning that's never externalized *and* leaves no trajectory trace is
**unrecoverable.** Ask-vs-infer has no free lunch; the evidence-tagging + derived-view approach
mitigates but doesn't eliminate it.

### 13.18 Belief revision representation (advances §9.1, §12)

**What's a belief — and what isn't.** Three revising kinds: decision-beliefs ("we use SQLite"),
preference-beliefs ("prefer composition here"), experiential observations ("the teardown is flaky").
**Exclusion:** *re-derivable structural facts are not beliefs* — "module M imports N", "this is
async" are re-parsed by Brain 2 on demand (§4/§5), never stored to drift. The belief layer holds only
**non-re-derivable** claims; this keeps it small (revision machinery is expensive).

**Backbone.** **R1** — versioned beliefs + **supersession edges carrying the reason** + valid-from/to
timestamps; current-truth = the un-superseded frontier (*a view*); history traversable. Boring/proven
temporal KG; the predecessor's temporal / memory-decay graph (§11) is a seed. **R2** — bi-temporal (valid-time vs.
transaction-time): principled ("what we believed in March" vs. "what was true in March"), likely
premature. **Never delete — supersede with reason** (preserves the rejected-alternatives jewels, §13.17).

**Detection (three tiers).** **D1 — explicit/announced** ("switch X→Y"): deterministic, gateway-visible;
easy. **D2 — structural audit (standout, deterministic):** a code-belief is continuously checked against
the AST — belief says "storage is SQLite" but the code now imports psycopg → **Brain 2 detects the drift
for free.** The two-hemisphere split doing differentiated, largely-undone work; build early. **D3 —
semantic** (fuzzy: "prefer X"…"prefer Y", no structural footprint): unreliable; use a dreaming-pass LLM
check over **graph+embedding-narrowed candidates** (O(n²) → narrow to same-entity/topic + close first).

**Reconciliation — conservative, because false supersession is a *silent poison*** (destroying true
knowledge is worse than a *flagged* stale belief). Auto-supersede only high-confidence D1/D2; D3 →
**propose, don't apply** (directed active query, §13.9); **prefer credibility-weighted coexistence over
deletion**; authority ordering (human-stated > agent-inferred, ties to §13.17 tags).

**Coexistence is active, not limbo.** Contradicting beliefs carry **credibility weights**, not a binary
flag. A **routine consolidation pass (dreaming)** revisits unresolved contradictions and *reweights* them
from accumulated outcomes + newly-arrived corroborating/conflicting memories — superseding/pruning only
when credibility decisively dominates (threshold), else adjusting weights. Brain-like: contradictions
resolve **over time with experience**, not by an upfront assumption. Credibility feeds retrieval (a
low-credibility belief surfaces less / with a stronger warning).

**Outcome-arbitrated.** Acting on a belief and observing the result shifts its credibility — the §13
external-outcome discipline applied to revision; competing beliefs are competing hypotheses reality
settles. Slow; needs the belief to be actionable.

**Query-time view:** current-validated / current-unverified / **stale-flagged** (D2 drift or
assumption-violation → surfaced *with warning*) / **superseded-historical** (surfaced *with its
supersession reason* — the §9.1 "used X until May, switched to Y because Z"). "Current beliefs" is a
*derived view* over the versioned graph (view-not-fact, per §13.16/§13.17).

**Earns-its-place gate.** (1) Is the history actually *used* (do agents benefit from "we rejected X
because Y")? (2) Do D2 + outcome-arbitration beat the boring baseline — MemGPT-style periodic LLM
flat-rewrite? **D2 is most likely to earn it** (deterministic, novel; flat-rewrite can't catch
AST-drift). Versioned-history + D3 are speculative → gate hard before committing.

### 13.19 Cross-hemisphere linking (advances §9.3)

The connective tissue several threads already lean on (footprint-join §13.16, structural audit
§13.18-D2, structural rung §13.10b, constraints §13.17). **The link:** an edge from an experiential
memory (episode/why/belief/preference) to **structural AST nodes** (file/function/class/module).

**Creation — deterministic backbone, mostly free.** Episodes link to the code they touched via the
§13.11b trajectory footprint (no inference); beliefs/whys via §13.10a symbol overlap or their
justifying episode's footprint. **Not everything links** — code-agnostic memory ("user prefers terse
commits") has no anchor; don't force it.

**Hard problem — the structural side is non-stationary** (re-parsed on every change). Linking by
transient node ID breaks every re-parse. Link by **symbol identity** (qualified path + git
rename-follow + AST matching), **re-resolved against the current AST each re-parse**. The target is an
*identity to re-resolve*, not a fixed location; resolution sometimes fails.

**Unification — link-resolution failure *is* the staleness signal.** When an experiential memory's
anchor can't be resolved in the current AST, the code it was about is gone/transformed → staleness
candidate. So **cross-hemisphere linking is the mechanism behind §13.18-D2** (they're one thing, not
two — strong coherence signal). A failed resolution is a *review flag*, never auto-delete (heavy
refactors throw false staleness; tolerable as a flag).

**Query-time, bidirectional.** *Structural → experiential* (headline): editing `teardown()` surfaces
the episodes/gotchas/decisions linked to it **and its k-hop neighbors** — HippoRAG-style associative
spreading; editing code pulls up *why it is the way it is* and *what bit us here before*. *Experiential
→ structural*: impact analysis for belief revision.

**Outcome-learning + complementarity.** Deterministic links = floor; **outcome-weighted link
credibility** = learned layer (useful links reinforced, spurious decayed — §13 discipline).
Structure-aware retrieval (graph spread) and learned-geometry retrieval (§13.4, embedding bend) are
**two complementary attacks on the §5.1 recall ceiling** — adjacency vs. outcome-bent semantics; they
miss in different places; use both.

**Hard problems:** symbol identity across heavy refactors is imperfect (noisy false staleness,
tolerable as flag); k-hop spreading over-surfaces in dense graphs (inherits the §13 ranking problem —
better *candidates*, not free answers); link granularity (function vs. package) open; only part of
Brain 1 links.

**Earns its place — more clearly than §13.18.** The deterministic backbone is nearly free and **three
mechanisms already depend on it** (§13.16 footprint-join, §13.18-D2 audit, structure-aware retrieval)
→ load-bearing infrastructure, build early. The learned link-weighting is gated like everything else.

### 13.20 Eval harness + pre-committed metrics (closes §12's last item; CLAUDE.md #4)

**Our predecessor project's actual failure was the metric itself** (self-referential reward → false "it's learning"
story). So the harness decides whether §13 is real or narrative. The **§13.11 logs *are* the eval
substrate** — log-obsessively and evaluate are two uses of one stream.

**Three levels.** **L1 — component/offline proxy:** `utility@k`, recall, recall-of-useful-but-distant;
needs labels we mostly lack → **hindsight relabels (§13.10) serve, on a held-out split** to avoid
training/eval circularity; noisy → *directional proxy only.* **L2 — task-outcome (truth, confounded,
slow):** pass/kept, time-to-resolution, dead-end count. **L3 — system ablation: brain-on vs.
brain-off (top-line)** — most external, hardest to game, our predecessor project's one supported result (+0.21–0.26);
can't run the same instance both ways → matched pairs / interleaving / held-out benchmark. Relationship:
**L3 truth-north, L2 per-task truth, L1 fast proxy; the proxy↔truth divergence monitor keeps L1 honest.**

**M-1 brain-on/off ablation — design examined (3-expert panel, 2026-06-17).** The naive
"gotcha-avoidance" ablation (L3 brain-on vs. brain-off) is **circular as first proposed**: if the
curated memory IS the warning, injecting it injects the answer; curating the test set conditions on
the brain having relevant content; no honest negative result is possible. The panel reframes it as
**M-1a — a conversion/delivery probe** (does surfacing the decisive memory cause an objective
behavior change, above generic salience?), which is a necessary-condition proof for the §13.10
prohibitive-memory path and a regression guard — **not** the thesis. M-1a hard gates: held-out
negative-control set (brain has no relevant memory → brain-on must NOT win — clear this null, not
zero); a generic-salience arm; a content-ablation arm (strip only the relevant memory); memories
must be episodes/why (reason-from, not warning-shaped, to prevent leakage); programmatic blind
judging (a code detector, not an LLM grading prose — firewall); pre-registration; per-case
anytime-valid stats (Beta-Binomial or equivalent). The better primitive to build toward is a
**within-task decision-point ablation** (at real dogfood decision points, two next-actions scored
by an external check — per-*decision* N, uncurated). Per-recall IPS is the right long-run ATE
estimator but blocked on R-7 volume. Pre-registration protocol: `docs/eval/m1a_preregistration.md`.

**Benchmark.** A frozen curated SWE-bench-style task set (poss. mined from project history) as the
*regression guard* + live longitudinal metrics as the *real-world signal*. Unavoidable tension:
**freeze (repeatable) vs. refresh (realistic)** — non-stationarity breaks comparability; manage, don't solve.

**Pre-committed exit-criteria rule (per layer, written *before* building):** rung N beats rung N−1 by
margin X on the proxy, proxy↔truth correlation intact, no L3 regression, within window W — or cut. The
harness **must run brain-off / each-rung / full**, so removable-and-measurable is a *switch*, not a vow.

**Integrity layer (guards conventional eval lacks — they measure whether the system fools itself):**
proxy↔truth divergence (§13.12), exploration regret (§13.9), segmentation footprint-coherence (§13.16),
belief false-supersession rate (§13.18).

**Honest problems:** single-user low-traffic → thin live L2/L3 → over-reliance on the benchmark →
**Goodhart on the benchmark** (multi-user resurfaces as the unlock); actuator non-determinism →
high-variance deltas → many runs → **expensive** (budget it); the thesis metric ("more use → more
useful") is **likely unmeasurable cleanly** (a positive slope confounds learning with easier-tasks /
changed-user) → fallback: measure the **L3 delta at intervals**, accept the confounds; track **soft
signals** (time-to-resolution, dead-ends, human-supplied context), not just pass/fail.
