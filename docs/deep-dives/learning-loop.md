# Deep dive — Closing the learning loop: credibility, negatives, and proof

*Part of [Project Thalamus design notes](../design-notes.md). The **plan of attack** for the
credibility/learning track and the **"how we prove it"** companion to §13
([`outcome-learned-retrieval.md`](outcome-learned-retrieval.md) — "what we'd build") and §16.
Same gated style as [`security.md`](security.md). Worked through 2026-06-15.*

> **⮕ RE-AIMED 2026-06-15 — read this first.** The learning target moved from **code outcomes**
> (did committed work survive / tests pass) to **relevance credibility** (which memories are
> *current / used / important / well-connected*). Reason: outcome capture **doesn't accrue in this
> workflow** (tests run via plain `npm test`, no capture; much work is non-code), whereas
> **usage + supersession + recency + structure accrue every session**. So everything below about the
> *outcome* loop (churn / struggle / proxy↔truth on commits) is **PARKED** — built, dormant, kept for
> if heavy instrumented coding ever happens. What **carries over**: the **firewall** (elevate by
> external/behavioral facts — used / superseded / recent / central — never the model grading its own
> prose), the **modularity seams**, and the **research toolkit** (SNIPS / anytime-valid CIs / ranking),
> which re-target cleanly onto a *usage-based retrieval ranker*. New direction tracked in `ROADMAP.md`
> Track L. Already built toward it: supersession demotion (verified working), recency, cross-session
> reuse. The sections below remain the rigorous reference for *how* to build/measure a ranker safely.

---

## Why this doc exists

Credibility/learning is the next track. Two inputs forced a reshape of the spec'd A→B→C plan
([`dreaming.md`](dreaming.md) "fate-based credibility"):

1. An outside review that cleanly separated the **useful tool now** (the structural hemisphere,
   real today) from the **unproven thesis** ("more use → more useful"), and decomposed "close the
   loop" into four stacked blockers — mechanical (join keys), statistical (no negatives), **causal**
   (observational logs can't show causation), and scale (single-user n=1).
2. The operator's correction: fix-forward dev produces ~no `git revert`s, so **revert-as-negative
   was the wrong signal** (which is why it was never built).

The conclusion that reshapes everything: **credibility is not a ranking feature gated on un-blinding
a monitor. It is a labeling problem and a causal-measurement problem, with re-ranking as the last,
most-gated payoff.** This doc pins that so we build in the right order.

## The reframe

The spec'd sequence — **A** compute fate → **B** un-blind the proxy↔truth monitor → **C** recall
re-ranks by credibility — is correct but mis-weighted:

- **A computes over an empty input.** Fate today sees only supersession + revert, both ~absent in a
  fix-forward solo repo. Credibility is inert not because the re-ranker is missing, but because the
  *signal* is (`churn_ratio` / `survived_activity` are stubbed empty in `FateContext`).
- **C hides a self-reference trap.** A fate-trained re-ranker measured by a fate-based metric scores
  the model against its own training signal — the quiet version of the Polynoica trap.

So the hard, build-first parts are the **input** (negatives) and the **validation** (causal). The
re-ranker is last.

---

## The plan of attack (build in this order)

### Pre — Join keys + decision logging  *(mechanical; unblocks everything)*
- **Goal:** any outcome can attach to any recall, and the option of counterfactual evaluation is kept
  open.
- **Build:** serve mints a session id at process start → `.thalamus/session/current.json` → pytest
  plugin + git sync stamp TEST_RUN / COMMIT events with it; the time-window attribution join is the
  resilient fallback for the shared-HTTP multi-agent case.
- **Log now (expensive to retrofit — propensities cannot be recovered after the fact):** per shown
  item, the **realized propensity** (not the `1.0` placeholder), the **ranker/config version**, the
  full **candidate set + decision-time features**, and the **rank shown**. The research pass found
  that deterministic top-k logging (`propensity=1.0`) has *no common support*, which makes all
  off-policy estimation **undefined, not just noisy** — so even before we build any estimator (Step
  3), we must (a) stamp the ranker version on every event [free "intervention-harvesting" data across
  reships] and (b) add a small flag-gated randomization knob (FairPairs/ε swap) so realized
  propensities are real.
- **Done when:** new retrieval events carry a session key (today 61/110 are null) and a ranker-version
  + propensity field; verdict `n_units` rises above ~1 on keyed sessions.
- **Firewall:** keys + mechanical logging only — no signal interpretation here.

### Step 1 — The negative-signal labeler  *(the real first build)*
- **Goal:** make "fate" carry information in a fix-forward repo.
- **Why:** reverts ~never happen; **the negative is the *rewrite*, not the revert.** The dead-end is
  spelled as churn/overwrite in the diff, not as a `git revert` event. This fills the stubbed
  `churn_ratio` / `survived_activity` fields.
- **Build** a survival-vs-overwrite labeler over git diffs + the trajectory log, **region-level**
  (symbol/hunk, not file), in rough order of cleanliness:
  1. **Soft-revert** — lines added in commit A, deleted in commit B shortly after. Pure diff,
     cleanest, needs no special workflow.
  2. **Same-region thrash** — one symbol/hunk reworked across several commits (not "the file changed"
     — every active file changes; the discriminator is the *region*).
  3. **Fix-shaped commits on recent code** — a `fix:` / "oops" / "actually" commit touching a region
     a recent commit introduced retroactively labels the earlier region defective (conventional
     commits = a free label).
  4. **Within-session struggle** (no commit) — repeated failed test runs on one target, green→red
     transitions, error loops, long time-to-green. Already in the trajectory log; never touches git.
  5. **Cross-session fix-linkage** — a later fix on code an earlier episode introduced attributes a
     negative back via footprint overlap (fix-forward devs fix forward — the fix *is* the signal,
     linked backward in time).
- **Positive label = *exercised* survival:** a region committed, not re-touched, that shipped and
  stayed — **and was subsequently exercised** (touched by later work, or covered by tests that ran).
  Plain survival is a *biased positive* (code can "survive" merely by being ignored); requiring it to
  have been exercised raises the signal-to-noise of the positive class.
- **Validate the ruler before you measure with it.** The labeler is a heuristic and becomes our
  "truth" signal — so it needs its own validation, or a noisy heuristic silently validates the model
  (the self-reference risk, relocated). Hand-label a small **gold set** of region-fates and measure
  the labeler's **precision/recall** against it before trusting its output downstream.
- **Done when:** `FateContext.churn_ratio` / `survived_activity` are populated from real history,
  `assess_fate` weights them, the labeler clears a pre-set precision/recall bar on the gold set, and
  the credibility distribution over the curated backlog shows real spread (not all-UNKNOWN).
- **Firewall:** reads diffs + test-run events (external acts), never the memory's prose or the
  model's opinion.
- **Seam:** behind an `OutcomeLabeler` protocol — the heuristic is v1; an LLM-judge-with-firewall or a
  better diff model drops in without touching anything downstream.

### Step 2 — Un-blind the monitor  *(credibility B)*
- **Goal:** prove the proxy↔truth monitor can — or honestly cannot yet — discriminate, now that
  negatives exist.
- **Build:** deploy the Pre + Step-1 capture and let keyed sessions with real outcomes accrue (a
  serve restart just picks up the new code — accrual is the mechanism, not the restart); report
  `monitor_with_fate` vs `monitor_without_fate`; read the alignment + the reward-hacking flag.
- **Done when:** the verdict shows a non-degenerate outcome distribution (successes AND failures)
  **on ≥ N pre-registered units**, reported **with an uncertainty interval** — never a point estimate
  (at this volume a bare "alignment ≥ 0" flips on one session). Even "no signal yet, CI spans 0" is a
  real, honest read.
- **Honest limit:** B is still **observational** — survived-vs-overwritten sessions *co-occur* with
  recalls; they are not randomized. B tells you the proxy isn't lying relative to fate; it does NOT
  establish that the brain *caused* the outcome (that is Step 3's job, and the win condition's
  corroboration must come from Step 3, not from this observational alignment).

### Step 3 — The counterfactual instrument  *(the part that decides truth)*
- **Goal:** ask "did the brain *cause* the better outcome," not just "did it co-occur." This is §13's
  calibrated exploration, pulled into the critical path because **Step 4 cannot be validated without
  it.**
- **Build (cheap → real), lean offline first** *(defaults chosen via the 2026-06-15 literature review;
  all behind the seams below):*
  - **Temporal hold-out** — credibility at time T predicts *future* fate at T+1. Offline, no live
    cost, and **non-circular** — but **predictive, not causal**: it kills the circular-validation
    trap, it does *not* by itself prove the brain *caused* better work. Do first.
  - **Off-policy estimation (SNIPS) — the default offline causal leg.** Over the logged recall
    perturbations (the Pre-step propensities + ranker-version harvesting), self-normalized IPS with
    exposure-aware propensities + weight clipping is the consensus low-n-robust estimator; DR /
    DR-shrinkage stay swappable for when an external reward model + more data exist. Always report
    effective sample size + bootstrap CIs, never a point estimate.
  - **Interleaving — demoted to a swappable complement, NOT the default.** The review found it's a
    poor fit for us: its credit machinery assumes an immediate *click* to attribute, but our outcome
    is a **delayed, sparse per-edit fate** with no per-item click; and its famous efficiency
    multiplier is *relative* — our absolute volume stays tiny. Keep its within-context paired-variance
    idea as a design principle, and keep an interleaving rung behind the seam for any future
    immediate used/not-used sub-signal. (Low-regret online option if wanted: Oosterhuis "LogOpt".)
  - **Session-level on/off A/B** — genuine causal estimate; taxes the live brain (at n=1 you're
    experimenter *and* subject), so opt-in/later.
- **Done when:** at least one non-circular validation path produces an outcome-delta estimate with a
  propensity-weighted log + an anytime-valid interval behind it.
- **Firewall:** exploration varies what's *shown*, judged by external outcomes — never by the model
  scoring itself.

### Step 4 — The re-ranker  *(credibility C — last, most-gated)*
- **Goal:** recall down-weights low-credibility memories; belief reconciliation consumes credibility.
- **Gate:** hard-gated on Steps 1, 2, **and** 3 — specifically on a *non-circular* validation
  existing (see the circular-validation trap).
- **Build:** a durable per-memory credibility store; an **ablatable** credibility-aware retriever rung
  behind the `core.Retriever` seam.
- **Measured two ways, tiered honestly:**
  - **L1** (cheap, mostly built — the `compare` ablation + `probe-eval`): does it surface
    better-*future*-fated memories higher? Measured by **temporal hold-out**, NOT by the same fate it
    trained on.
  - **L3** (hard, needs volume + Step 3): does that improve task outcomes? The only thing that
    validates the thesis.
- **Done when:** the rung beats the L0+hybrid stick on the pre-registered metric via a non-circular
  path — or is cut.

---

## The pre-registered win condition  *(LOCKED 2026-06-15)*

Framed as **experiment design at n=1**, not "pick a meaningful number." At single-user scale the
binding constraints are **statistical power** (the metric must move often enough to test) and
**Goodhart-resistance** — so the lessons are: *no composite primary* (a composite hides which part
moved and hands you two knobs to rationalize with), and *a sensitive decision metric and an aligned
validation metric are not the same number*. That pairing is exactly the proxy↔truth machinery we
already have — so we pre-register the **pair + an alignment win condition**, not one outcome number:

- **Decision metric (proxy — chosen for sensitivity):** per-recall **`utility@k`** over recalls that
  fed real work. Leading, frequent, accrues fast — read every cycle.
- **Validation metric (truth — chosen for alignment):** **per-edit fate** from the Step-1 labeler —
  {survived / overwritten / dead-end}, footprint-attributed. Lagging, accrues slowly.
- **WIN CONDITION (amended — see below):** a rung "works" **iff a proxy gain is corroborated by a
  *Step-3 (counterfactual)* alignment whose anytime-valid *confidence sequence* excludes 0, with the
  reward-hacking flag off.** While the sequence still contains 0 → **no verdict** (a first-class
  state, not a fudge). A proxy gain whose alignment is flat/negative, or whose sequence brackets 0
  once it tightens, is **reward-hacking → cut**.
- **Unit of analysis:** **per-edit / per-recall**, never per-session — the single biggest power lever
  at single-user scale.
- **Guardrails (secondary, logged, never the win condition):** time-to-green, raw edit-survival rate,
  redundant-re-investigation rate.

This is both better experiment design and a perfect fit for the firewall: the proxy never validates
itself, and "truth" is an external act (the Step-1 labeler's region fate), not the model's opinion.

> **Two amendments (2026-06-15, pass 2) to the bare "alignment ≥ 0" first lock:**
> 1. **Anytime-valid uncertainty, not a point estimate.** "≥ 0" on a handful of units is noise — and
>    because we'll peek at the data daily as it trickles in, a *fixed-n* CI recomputed repeatedly is
>    *invalid* (peeking inflates error ~5×). The win requires an **anytime-valid confidence sequence**
>    (betting CS, Waudby-Smith–Ramdas) that **excludes 0**; "still contains 0" = underpowered, *no
>    verdict*. This is the only form of "CI excludes 0" that survives continuous monitoring.
> 2. **Causal source, not observational.** "Corroborated by alignment" means the **Step-3
>    counterfactual** alignment (temporal hold-out / off-policy estimate), **not** the Step-2
>    *observational* monitor — otherwise the causal gap we built Step 3 to close silently reopens.
>
> *(Earlier supersession note still stands: this replaces the "edit-survival + dead-ends composite"
> recommendation — a composite is ungameable-to-interpret; survival alone is lagging/biased/insensitive.)*

## What can and cannot be proven offline  *(the L1/L3 line)*

The offline transcript replay (`probe-eval`, the `harness.compare` ablation) is **strictly L1**: it
scores retrieval *surface quality* (did the brain surface what turned out to matter) on **fixed**
transcripts, brain-on vs brain-off. It is real, cheap, the n=1 unlock for retrieval quality — and it
is how we'd prove the **structural-hemisphere floor** the whole viability case rests on. It is **not**
outcome proof: a fixed transcript's actions are frozen, so you cannot read a *new* outcome from it.

**The research pass sharpened this from "two tiers" to "two quantities known to diverge."** The RAG/IR
literature repeatedly shows retrieval metrics do *not* predict downstream task quality — *Lost in the
Middle* (position, invisible to recall@k, drives usage), *The Power of Noise* (topically-related-but
-answerless docs *hurt* while random ones can help), and a utility-tuned metric (UDCG) reaches only
~0.36 correlation with end-task accuracy. So **closing L1 is not a down-payment on L3.** Treat the
offline replay as a **regression guard / sanity check** (how Netflix/Pinterest use offline replay —
known to be biased *upward* by presentation + feedback-loop effects), never as evidence of usefulness.
The outcome question (L3) needs the Step-3 counterfactuals + real volume. **Do not conflate "the brain
surfaced the right thing" (L1) with "the brain made the work better" (L3) — the field's hard-won
lesson is precisely that they come apart.**

## Traps and limits  *(write them down so they're known, not surprises)*

- **Circular validation** — a fate-trained re-ranker measured by a fate-based metric measures the
  model against its own training signal. Step 4 is gated on a non-circular validation (temporal
  hold-out or counterfactual) for exactly this reason.
- **Attribution transfer** — credibility moves a *code region's* fate onto a *memory's* credit via
  footprint overlap. A region that churned despite the brain surfacing the right memory could mean
  the memory was wrong, *ignored*, or the task was just hard — footprint overlap can't tell these
  apart; only Step 3 can. Credibility's core number is only as trustworthy as Step 3.
- **Invisible negatives** — subtly-wrong code that ships and is never fixed produces no signal, so
  **survival is a biased positive** (survived = not-caught, not = correct). An irreducible ceiling on
  any outcome-learning system.
- **Churn noise** — a region changing again is sometimes a correction (negative), sometimes iterative
  construction (neutral). Lean on the region-level discriminators + volume + offline tuning, never a
  single rewrite event.
- **The proxy chain** — "truth" itself is a proxy: `utility@k` → per-edit fate → *actual value*. We
  validate link 1 (proxy↔fate); link 2 (fate↔value) is *assumed*. So per-edit fate is the best
  external signal we have, not value itself — don't over-trust it, and keep the assumption visible.

## Decision rules (stopping / escalation)  *(provisional — needs ratification)*

The single biggest risk to this whole track is **perpetual gating**: waiting forever for a signal
single-user volume may never produce. So we pre-commit an exit. *Provisional* default (recommended,
pending sign-off):

- **Stop rule:** after **M pre-registered per-edit units** (or T elapsed) with the Step-3 alignment CI
  still spanning 0, declare the loop **unprovable at this volume** — do not keep iterating on it.
- **Escalation branch (on "unprovable"):** **ship credibility as an explicitly-labeled *unvalidated
  prior*** behind the firewall — useful as a soft signal, **not claimed as validated learning** — and
  revisit only if multi-repo / multi-user volume arrives. *(Alternatives: keep it off until volume; or
  recruit more repos to force variance. To be ratified.)*
- The reward-hacking flag stays the hard guard throughout: a proxy gain without causal corroboration
  is never shipped *as learning*, whatever the stop rule says.

## Modularity & swap-points  *(build it so pieces drop in/out)*

Every step is a **removable, measurable layer behind a clean seam** (§14.5) — so a better method (often
one the literature review surfaces) swaps in without redoing the rest. The seams:

| Component | Seam / protocol | Default (chosen) → alternatives (swappable) |
|---|---|---|
| Negative-signal labeler (Step 1) | `OutcomeLabeler` | diff/trajectory heuristic → LLM-judge-with-firewall / better diff model |
| Credibility store (Step 4) | `CredibilityStore` | in-memory → Neo4j-persisted |
| Counterfactual estimator (Step 3) | `OffPolicyEstimator` | **SNIPS** (exposure-aware, clipped) → DR / DR-shrinkage; interleaving rung for any immediate-signal case |
| Statistical test (win condition) | `SignificanceTest` | **betting confidence sequence** (Waudby-Smith–Ramdas; ship a ~15-line dependency-free Hoeffding CS first) → variance-adaptive CS; fixed-n CI kept off-by-default as a "how badly naive peeking misleads" comparator |
| Re-ranker (Step 4) | `core.Retriever` (existing) | already ablatable — credibility rung on/off |
| Validation harness | `eval.harness.compare` (existing) | brain-off / L0 / L0+rung, any rung pluggable |

`SignificanceTest` interface is tiny — `update(paired_diffs) -> (lower, upper, decided: bool)` — so
the Hoeffding CS ships first and the variance-adaptive one is measured against it on real logs.

Rule of thumb: **nothing downstream may import a concrete labeler/estimator/test** — only its protocol.
That is what lets the research pass change our minds about Step 3/4 *methods* without touching Steps
1–2 or the gateway.

## The firewall, restated for this track

Every signal at every step is an **external act** — a diff, a test-run event, a kept-vs-overwritten
region, a propensity-logged exploration outcome. Nothing reads the memory's prose, sentiment, or the
model's embedding geometry as a label. **Credibility never validates itself:** the training signal
(fate) and the validation signal (future fate / counterfactual outcome) are kept distinct. This is
what makes our version safe where Polynoica's was not.

## Relationship to existing components

- `experiential/fate.py` — `FateContext` (the stubbed churn/survival fields Step 1 fills),
  `assess_fate`.
- `dreaming/credibility.py` — the pass that today only logs the distribution; Steps 1–4 give it real
  input + a durable consumer.
- `eval/harness.py` `compare` + `cli/probe_eval.py` — the L1 ablation (mostly built); the measuring
  stick for Step 4's L1 tier.
- `eval/proxy_truth.py`, `cli/verdict.py` — the monitor Step 2 un-blinds; the reward-hacking flag is
  the Goodhart guard for Steps 3–4.
- §13 outcome-learned retrieval (what we'd build), §14.2 firewall, §16 roadmap, ROADMAP.md Track L.

## Open questions

- Unit of analysis (per-edit vs per-session) for powering a statistical test sooner.
- More repos (more variance) vs depth on one — which buys discrimination faster.
- How aggressively to explore (Step 3) without degrading the live brain's usefulness.
- Whether the thesis slope is cleanly measurable at single-user scale at all, or only as
  interval-deltas + soft signals.
- **Low-volume causal attribution is a genuine open frontier** — the literature solves it only with
  many subjects or many events; nobody has cracked it at single-dev scale. Our realistic stance:
  paired/withhold-recall + variance reduction + patience + honesty that n=1 can't yet carry a causal
  claim (→ the provisional stopping rule).

## References  *(from the 2026-06-15 literature review)*

**Off-policy / counterfactual evaluation:** Swaminathan & Joachims, *Self-Normalized Estimator*
(NeurIPS 2015); Dudík et al., *Doubly Robust Policy Evaluation* (ICML 2011); Su et al., *DR with
Shrinkage* (ICML 2020); Agarwal et al., *Position Bias without Intrusive Interventions* (WSDM 2019) +
Fang et al., *Intervention Harvesting* (SIGIR 2019); Saito et al., *Robustness of OPE* (RecSys 2021);
Bottou et al., *Counterfactual Reasoning* (2013).
**Interleaving / online IR eval:** Chapelle, Joachims, Radlinski & Yue, *Large-Scale Validation of
Interleaving* (TOIS 2012); Radlinski et al. team-draft (CIKM 2008); Radlinski & Craswell, *Optimized
Interleaving* (WSDM 2013); Hofmann et al., *Fidelity/Soundness/Efficiency* (TOIS 2013); Oosterhuis &
de Rijke, *Taking the Counterfactual Online / LogOpt* (ICTIR 2020).
**Anytime-valid inference:** Howard et al., *Confidence Sequences* (arXiv:1810.08240); Johari, Pekelis
& Walsh, *Always-Valid Inference / Peeking at A/B Tests* (Operations Research 2021 / KDD 2017);
Waudby-Smith & Ramdas, *Estimating Means by Betting* (JRSS-B 2024, arXiv:2010.09686); Ramdas et al.,
*Game-theoretic statistics / safe anytime-valid inference* (Statistical Science 2023); lib: `confseq`.
**The retrieval↔outcome gap & dev productivity:** Liu et al., *Lost in the Middle* (TACL 2024);
Cuconasu et al., *The Power of Noise* (SIGIR 2024); RAGAS (arXiv:2309.15217) + GroUSE/ARES critiques;
UDCG (arXiv:2510.21440); METR *AI dev productivity RCT* (2025, found −19% with +20% perceived); Peng
et al. Copilot study (2023, +55%); agent-memory: Generative Agents (2023), MemGPT/Letta (2023), A-MEM
(NeurIPS 2025).
