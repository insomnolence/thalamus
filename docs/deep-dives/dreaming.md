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

## Open questions remaining

- Damping/hysteresis design for cross-cycle stability (hard part #2).
- Concrete eval of consolidation at single-user scale (hard part #3).
- The reflection pass's exact gate and representation (semantic-principle node schema).
- Interaction with belief credibility decay (does decay touch beliefs, or only episodes?).
