# M-1a — pre-registration protocol (gotcha-conversion probe)

*Written 2026-06-17, BEFORE any run. Pre-registration is a hard gate (the M-1 expert panel +
CLAUDE.md §4 "define eval metrics before building"). **Freeze/commit this before the first run** —
its credibility depends on cases, oracles, trials, statistic, and threshold being fixed in advance.
Design rationale: `retained:5023addb`. Do not add/drop cases or change the statistic after seeing
results.*

## What this measures (and what it does NOT)

**M-1a is a conversion / delivery probe, NOT the thesis ablation.** It answers one narrow,
falsifiable question:

> *Conditional on the brain holding the decisive memory for a task, does surfacing it cause a fresh
> actuator to take an objectively better action — above and beyond mere salience?*

It is a **necessary-condition proof + regression guard** for the §13.10 prohibitive-memory delivery
path (the hardest retrieval class: a constraint that manifests as an *absence*). It closes the
M-2(surfaced) → behaviour(used) gap.

**It does NOT license:** "the brain improves task success" (wrong estimand — conditioned on the brain
winning), "more use → more useful" (no accumulation axis), or any claim about the real task
distribution (curation removed the task-distribution sample). A green M-1a must **never** be quoted as
the thesis verdict.

## The estimand & the strongest claim licensed

> On a pre-registered set of (task, held-decisive-memory) cases, serving the relevant memory causes a
> fresh actuator to take the gotcha-avoiding action at paired rate δ (anytime-valid CI […]) more than
> not serving it — **and this δ clears the negative-control null and the generic-salience null**. The
> retrieval→actuator delivery link produces an objective behaviour change when the brain holds the
> decisive memory.

## Arms (4) — identical prompt envelope, differ ONLY in the context block

The actuator gets a uniform template (`"… here is context: <BLOCK> …"`); the block differs by arm.
Length/format-match the blocks so "more tokens" is never the cue.

1. **`off`** — no memory (length-matched placebo context).
2. **`salience`** — `off` + a generic, content-free nudge ("watch for edge cases / known gotchas").
   The brain's win must survive subtracting this (kills the "any hint raises salience" effect).
3. **`content-ablation`** — the full brief **minus the one relevant memory**, everything else identical.
4. **`full`** — the full brief (the relevant memory present).

**Primary contrast:** `full` − `content-ablation` (isolates *the memory*, not the brain-as-bundle).
**Secondary:** `full` − `off` (the brain-as-bundle question). **Salience gate:** `full` must beat
`salience`, not just `off`.

## Case sets

- **Positive set (N = 10–15):** (task, memory) where the brain holds the **decisive** gotcha. Each
  case carries: a task cue from an **independent source** (the original commit/issue text — NOT
  co-authored with the gotcha); the memory id (independently judged relevant, per the
  `eval/plan_brief.py` anti-circularity bar); and a **deterministic hit-oracle** (below). **The memory
  must be an episode/why/decision — NOT a warning-shaped answer key** (so the actuator must *reason
  from* it; a memory that states the avoidance verbatim = answer leakage → excluded).
- **Negative-control set (N = 6–10), MANDATORY:** matched task style, brain holds **no** relevant
  memory (brief may still fire with general context). **Expectation: `full` ≈ `off` (δ ≈ 0).** The
  positive δ must **clear this null**, not merely clear zero. If brain-on wins here, the positive
  result is salience/length/demand artifact — disbelieve it.
- **Adversarial set (N = 3–5), optional:** brain holds a *stale/wrong* belief about the target. Does
  `full` make it **worse**? Probes the supersession failure mode; keeps the set from being a highlight reel.

## Judging — programmatic & blind (firewall)

The "did it take the gotcha action?" verdict is **code, not a prompt**: a test / lint / AST check /
regex authored *with the case* that fires iff the gotcha was hit. Prefer cases with a deterministic
oracle; exclude cases that can't get one (that exclusion keeps the firewall intact — it is not a
coverage gap to paper over with an LLM-judge). An LLM-judge is allowed ONLY as a blind, arm-stripped
pre-screen (§13.9 Layer-3), never as the verdict. No model grades its own (or a sibling's) prose.

## Trials & statistic

- **Trials:** 8 per arm per case, varied seed/temperature, **fixed in advance** (no optional stopping).
- **Unit of analysis: the CASE, not the trial** (trials are repeated measures → pseudoreplication if
  pooled). Per case: avoid-rate per arm.
- **Headline:** paired `δ_i` across cases. **Sign test** on `sign(δ_i)` (primary, distribution-free) +
  **Wilcoxon signed-rank** (uses magnitude). **Cluster bootstrap** (resample cases, then trials) for a
  90% CI on mean δ. **Hierarchical Beta-Binomial** posterior for `P(δ>0)` + credible interval (the
  honest low-N summary). Report mean δ + CI + per-case win count k/n — **never a bare win count or p**.
- **Anytime-valid CI** (betting/e-process) since we will peek as cases accrue.

## Success criteria (pre-committed)

A POSITIVE M-1a requires ALL of:
1. `full` − `content-ablation` mean δ > 0 with the 90% CI excluding 0 (primary contrast).
2. Positive-set δ **>** negative-control-set δ (clears the placebo null).
3. `full` **>** `salience` (the win survives subtracting salience).
4. No post-hoc case/trial selection; the oracle and arms were frozen here first.

If the win vanishes once the negative-control and salience nulls are subtracted, **M-1a measured the
tautology and we caught it** — that is the instrument working, not a failure.

## Build dependencies (what M-1a needs that doesn't exist yet)

- An **actuator-in-the-loop harness**: consumes the served context block → drives a fresh actuator →
  emits an action, per arm, per trial. (`eval/harness.compare` + `NullRetriever` give the *retrieval*
  on/off switch; this is the missing *actuator* half — shared with the later decision-point primitive.)
- **Per-case programmatic detectors** (the oracles).
- A **cases file** (`docs/eval/m1a_cases.json`) authored to this protocol, frozen before runs.

## Graduation (beyond M-1a)

M-1a is the curated special case of the **within-task decision-point ablation** (per-decision N,
uncurated, recovers external validity) — build toward that as dogfood accrues. The true-thesis
per-recall IPS estimator is gated on **R-7** (stochastic serving + logged propensity); build that
logging in parallel (this doc's sibling track).
