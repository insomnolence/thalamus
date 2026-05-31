# Deep dive — Dreaming / consolidation (the offline integration pass)

*Part of [Project Thalamus design notes](../design-notes.md), worked through 2026-05-24. Cross-refs
of the form `§13.x` (written **OLR §13.x**) point to [`outcome-learned-retrieval.md`](outcome-learned-retrieval.md);
§1–§12, §14, §15 point to [`../design-notes.md`](../design-notes.md).*

---

## Scope: dreaming is a scheduler, not a monolith

The offline, non-time-critical pass between the fast episodic store and the slow semantic store — the
Complementary Learning Systems "replay" template (design-notes §6). Over the OLR dive, dreaming accreted
a pile of obligations and became a god-object (audit finding #4). The fix is structural:

> **Dreaming is an offline orchestrator running a DAG of independent, individually-gated,
> individually-removable passes over the immutable raw log.** Each pass reads raw (+ current derived
> state), writes a *derived view*, is deterministic where possible, and is measured/removable on its own.

There is no monolithic "consolidation" to reason about — a scheduler + a set of transforms, each living
or dying by its own gate (principle 5). "Dreaming" = the fact they run offline, off the critical path.

## The pass DAG (ordering + dependencies)

```
segmentation (S2 footprint)              ← root: everything operates on episodes
   ├─→ why-reconstruction
   ├─→ hindsight relabeling               → training pairs (a learning feed, not stored memory)
   └─→ cross-hemisphere link-resolution   → staleness flags
                  ↓
        belief reconciliation             ← consumes why + staleness(D2) + accumulated outcomes
                  ↓
   ├─→ connection discovery (GNN/PageRank)    → proposed links
   └─→ reflection / abstraction               → proposed principles
                  ↓
            decay + prune                  ← runs last, on the consolidated state
```

**Segmentation is the root** (beliefs depend on episodes); **link-resolution feeds belief reconciliation**
(its staleness flags *are* the OLR §13.18-D2 audit); **decay/prune runs last**. Within a run it is an
**acyclic DAG**. *Across* runs it is a **feedback loop** (this cycle's consolidation → future retrievals
→ future outcomes → future relabeling) — that loop *is* the "more use → more useful" thesis; within-run
acyclicity keeps each run tractable.

## Per-pass: deterministic/LLM, cadence, firewall, earns-its-place

| Pass | Det / LLM | Cadence | May… | Earns place vs. flat-rewrite baseline |
|---|---|---|---|---|
| Segmentation (S2 footprint) | Det | triggered / batch | **act** | likely — structural work a rewrite can't do |
| Link-resolution + staleness | Det | **eager** (every Brain 2 re-parse) | **act** | likely — *is* the D2 audit |
| Hindsight relabeling | Det mining | batch (settled episodes) | **act** (emits training pairs, gated by net-outcome) | learning feed, gated by OLR §13.12 |
| Belief reconciliation D1/D2 | Det | eager / batch | **act** | likely |
| Fate-based credibility / recorded-outcome | Det (objective) + propose-only (text/LLM) | batch (settled history) | **act** (objective fate) | the Tier-2 negative+graded producer (§13.17) |
| Why-reconstruction | LLM | batch | **propose** only | must prove |
| Belief reconciliation D3 | LLM | batch | **propose** only | speculative |
| Connection discovery | learned (GNN/PageRank) | batch | **propose** only | gate hardest / latest |
| Reflection / abstraction | LLM | batch | **propose** only | must prove (noise risk) |
| Decay + prune | Det | periodic | **act** (archive, *not* delete) | likely |

**Cadence falls out of the deterministic/LLM split** — it's the same axis: cheap deterministic passes
run **eager/triggered** (staleness should be fresh on every code change); expensive LLM passes **batch**
(they want *settled* episodes — reflecting over a half-finished episode is noise). Off the critical path
either way (OLR §13.9).

**Firewall (principle 3, audit #6):** deterministic passes may **act**; LLM/learned passes may only
**propose** — their outputs are retrievable/proposable but gain credibility *only from external outcomes*,
never self-validation.

## The safety property: dreaming is safe to get wrong

Dreaming writes only **regenerable derived views over the immutable raw log** (principle 1). A buggy
cycle or a hallucinating LLM pass does **not** corrupt truth — you **re-run from raw**. The one exception
is decay/prune, which is destructive → discipline: **archive / cold-store, don't delete**, keeping even
pruning reversible. A consolidation system that cannot catastrophically corrupt itself is rare; this one
gets it for free from capture-raw/derive-views.

## Honest hard parts

1. **Reflection is where dreaming most risks self-deception.** "Synthesize semantic principles" feels
   like intelligence and can produce plausible, hollow aphorisms — noise dressed as wisdom (the §5.4
   abstraction fix inverted). The firewall is the defense (a principle earns credibility only if
   retrieving it helps real outcomes); build it **last**, gate it **hardest**.
2. **Cross-cycle oscillation — undesigned gap.** Reweighting + re-segmentation each cycle can make
   credibility bounce and segmentation flip run-to-run. Needs damping / hysteresis / stability
   thresholds. Not designed yet.
3. **Evaluating dreaming.** A/B with-vs-without consolidation (the OLR §13.20 L3 ablation, applied to
   dreaming) + held-out edge-prediction for connection discovery. Payoff is long-horizon; single-user
   signal is thin → multi-user resurfaces (design-notes §14).
4. **Novel framing** (vs. conventional consolidation = monolithic LLM rewrite): an offline **DAG of
   mostly-deterministic, individually-gated, removable passes** over an immutable log, **LLM passes
   fenced to propose-only**, **cadence from the det/LLM split**, **safe-to-rerun**. The *discipline* is
   the contribution, not the individual passes.

## Pass: fate-based credibility — the recorded-outcome / Tier-2 producer (designed 2026-05-30)

The pass that produces the **"accumulated outcomes"** the belief-reconciliation node consumes, and the
**negative + graded Tier-2 signal** the proxy↔truth monitor (OLR §13.12/§13.20) has been starved of.
Designed in a working session against the live dollhouse + dogfood brains; **supersedes the narrow
"detect git reverts / ingest CI" framing** of R3.

**The problem it solves.** Tier-2 capture was stuck: reverts ~never happen in a fix-forward workflow,
CI fires only on PRs (not normal commits), and a disciplined dev's *terminal* outcomes are
almost-all-positive **by construction** → binary pass/fail Tier-2 is structurally saturated, no
negatives, so proxy↔truth can't discriminate. The negatives don't live in terminal test results; they
live in the **fate of work over time**.

**The core move — read a memory's *fate*, not its *words*.** "Learn from itself" done safely is NOT
parsing memory text for good/bad sentiment (sycophantic, brittle, needs a hand-maintained keyword list
— and is the Polynoica self-reference trap the moment the model's *opinion* becomes its own label). It
is observing, deterministically, **what happened to each memory afterward** — an external fact, never
the model's prose. Author- and agent-count-agnostic: a single agent's "I had to redo X because it was
wrong" is the same signal as a reviewer's reject; the dev/reviewer setup is **one dialect, not the
design**. (An early text-outcome parser — `review_outcome.py` → `recorded_outcome.py` — was built
then **backed out 2026-05-30** as exactly the wrong direction: we read the *structural trace* of a
redo — a supersession / revert / churn — never the sentence describing it.)

**The bright line (firewall, §13.7 / principle 3):** learn from the recorded **event/action**
(reverted, superseded, redone, kept-and-reused), never the **adjective** ("good idea", "well written").
The memory text is the *discovery* channel; the grounded act is what *licenses* the label.

**Fate signals (by grounding strength → tier):**

| Signal | What it reads | Tier |
|---|---|---|
| Superseded? | a `SUPERSEDES` edge targets it (graph fact) | objective |
| Work reverted? | a sha it named was reverted (git) | objective |
| Recurring usefulness | later sessions recalled **and used** it (retrieval + usage/attribution logs) | objective |
| Longevity / survival | not superseded/reverted after substantial subsequent activity (graph + time/commits) | medium |
| Footprint churn | its code was heavily rewritten soon after (commit stream via the attribution map) | medium (noisy) |

This is almost verbatim the OLR §13.17 credibility formula — **longevity-without-supersession +
downstream success** — re-derived from "use the rich data we already have."

**Combiner → polarity (transparent, tunable, removable).** reverted → **negative**; superseded →
**negative** (the edge is a fact — the belief was revised away; no reason-text is read); reused ∨
survived-substantial-activity → **positive**; high churn-away → **weak negative**; otherwise
**unknown (excluded — never "good")**. Every signal is **objective** (an external fact); the combiner
is a small pure function; thresholds are config; components stay explicit (never collapsed into one
opaque score) so each is independently ablatable.

**Two consumers, one primitive.** Fate computed for:
- a **curated memory** → its **credibility** (feeds retrieval down-weighting + belief reconciliation) —
  the "learn from the memories" use;
- a **session's committed work** (the same revert/churn/survival machinery applied to the session
  footprint) → a **per-session Tier-2 outcome** for proxy↔truth — kept-vs-reverted-vs-churned grounded
  in fate over time rather than a point-in-time test.

**Cold start — near-zero for the signal that matters.** Three distinct lags: (1) *computation* (when
the pass runs — CLI on-demand / serve tick; same for any approach, and credibility is *slow-moving* so
tick staleness is invisible); (2) *signal-acquisition per memory* (a fresh memory has no fate until
later events accrue — event-driven, stays honestly "unknown"); (3) the **aggregate pattern** the
verdict/reconciliation actually need — available **immediately on first run over settled history**,
because the existing months of data already have settled fates. The backlog *is* the settled
population. A longitudinal/aggregate statistic is the *correct* shape; an instant per-memory grade is
the firewall-risky thing we must NOT want.

**Firewall placement / cadence.** A deterministic **actor** — every signal is an external fact
(graph/git/logs), so it may act (write the derived credibility view). A future LLM judge would be
**propose-only**, tiered below objective, and **calibrated against** the objective fate — it is not
built, and no current signal reads model prose. Batch cadence (wants settled history). Writes only a
regenerable derived view (re-runnable, idempotent — safe-to-rerun).

**Removable / measurable (the gate).** Each fate signal is an independent switch; the verdict can
report proxy↔truth with and without the fate layer, so we can see whether it *adds discrimination*
or just noise.

**DAG position.** Reads supersession + git + retrieval/usage logs; sits **before/feeding belief
reconciliation** (it produces the "accumulated outcomes" that node consumes) and the **verdict**. After
link-resolution (which supplies staleness).

**Honest limits.** Per-recent-memory cold start (above); supersession coverage is thin when agents
write *parallel* contradicting memories instead of `supersedes=` (dollhouse does this → churn/revert/
reuse carry more weight there); footprint-less memories (a preference, a non-code decision) lean on
longevity + recurring-usefulness only; churn is genuinely noisy (active dev churns code for good
reasons) → keep it weak, prefer change-*reversing* churn.

**Build sequence.** (1) pure fate primitives + combiner (no I/O, gated); (2) wire the fate sources
(supersession index, git, attribution map, usage log) + emit the derived credibility/outcome view;
(3) the `DreamingPass` + scheduler slot; (4) verdict reads session-fate Tier-2. **Validate on our own
brain first (dogfood), then a deliberate dollhouse serve restart** (the pass runs inside the
long-running serve, so dollhouse only picks it up on restart). *(Done; the early text-outcome parser
was backed out in step (1) — see the core-move note.)*

### Credibility: automatic vs manual, and the A/B/C roadmap (clarified 2026-05-31)

A long session got confused here, so, plainly:

- **"Dreaming" = the automatic interval process** — the `DreamTicker` inside the long-running serve
  (every `--dream-tick-minutes` + on each write). **`thalamus dream` is a manual dev/inspection
  tool** that runs one cycle and exits; it is *not* "dreaming" and does not count as the feature.
- **Fate = credibility = one engine** (`assess_fate` over superseded / reverted / reused / survived
  / churn). Two consumers: per-memory **credibility** (the `CredibilityPass`, a dreaming actor) and
  per-session **fate → Tier-2** (in the `verdict` report — a manual read-only *measurement*, not
  dreaming).
- The automatic dreaming loop runs (once the serve picks up this code): structural-refresh
  (**incremental** — only episodes new since the last tick, so no per-`remember` re-link storm),
  link-resolution, **credibility**, belief-audit.

**The roadmap — three steps; only A is done, C is the goal and is gated:**

- **A ✓ — credibility computed in the automatic loop.** The `CredibilityPass` runs on the
  `DreamTicker`, so the brain continuously scores each memory's fate. **Inert on its own** — nothing
  acts on the score yet (no recall change).
- **B — un-blind the proxy↔truth monitor.** Restart the serve (the supersede fix then lets
  supersessions actually *record* → fate **negatives** accrue) + accrue use → the monitor gets real,
  *discriminating* data (today it's starved: few units, ~all positive).
- **C — recall re-ranks by credibility (the goal: the brain reorganizes itself).** Recall surfaces
  high-credibility memories and demotes low-credibility ones. **Gated on B** — you do not deploy a
  feedback layer you cannot measure (§13.12: "if starved, *don't deploy a learned layer* — flying
  blind is the failure" — the Polynoica trap). When B gives the monitor eyes, turn C on *measured*
  (A/B recall with vs without), **conservatively — never suppress a memory that is merely NEW**
  (unknown fate), only confirmed-bad.

Doing **C before B is the one thing not to do.** A is safe and done; B is enabled by the deliberate
serve restart; C is the payoff, on the far side of a working monitor.

## Open questions remaining

- Damping/hysteresis design for cross-cycle stability (hard part #2).
- Concrete eval of consolidation at single-user scale (hard part #3).
- The reflection pass's exact gate and representation (semantic-principle node schema).
- Interaction with belief credibility decay (does decay touch beliefs, or only episodes?).
- Fate-combiner thresholds (survival horizon, churn ratio, reuse count) — tune on the settled backlog;
  report the verdict with/without each tier to see which fate signals actually carry discrimination.
- Session-work fate vs. memory credibility — same primitive at two granularities; confirm the
  session-footprint → per-session Tier-2 mapping for proxy↔truth.
- Calibrating the model-text tier against objective fate (does "looks-wrong" coincide with reverted/
  churned?) — the antidote to sycophantic self-judgement; needs enough overlap to measure.
