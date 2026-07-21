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

### As-built pass order (v0 scheduler)

The v0 `Scheduler` in `build_dream_scheduler` (`packages/cli/src/thalamus/cli/dream.py`) flattens the
design DAG to a linear sequence in topological order. The current sequence:

```
structural-rederive        ← re-derive Brain 2 from source (hash-gated; runs first so new nodes exist)
structural-refresh         ← re-link episode footprints to current modules (needs fresh nodes above)
link-resolution            ← refresh superseded frontier + staleness flags
attribution-refresh        ← re-derive footprint usage attribution from the live graph + logs
                              (AFTER re-derive/re-link; BEFORE usage-refresh which consumes it)
behavioral-consolidation   ← fold the log WAL's used-session sets into the behavioral store
                              (AFTER attribution; BEFORE usage-refresh reads the store)
usage-refresh              ← swap fresh usage weights from the behavioral store into the rung
centrality-refresh         ← recompute structural-centrality weights (reads freshly re-derived graph)
cochange-refresh           ← refresh the plan tool's file co-change index from new commits
credibility                ← assess each curated memory's fate-based standing
belief-audit               ← propose-only supersession suggestions (proposer, last)
```

The ordering constraint that matters most for the three new passes: **attribution-refresh must run after
re-derive + re-link** (it reads the live graph) and **before behavioral-consolidation** (which folds the
fresh attribution into the store), which must run **before usage-refresh** (which reads the store).
Together they form a three-step pipeline: re-derive the attribution signal → accumulate it into the brain
→ serve it to the rung.

## Per-pass: deterministic/LLM, cadence, firewall, earns-its-place

| Pass | Det / LLM | Cadence | May… | Earns place vs. flat-rewrite baseline |
|---|---|---|---|---|
| Segmentation (S2 footprint) | Det | triggered / batch | **act** | likely — structural work a rewrite can't do |
| Link-resolution + staleness | Det | **eager** (every Brain 2 re-parse) | **act** | likely — *is* the D2 audit |
| Attribution refresh | Det | **eager** (every maintenance tick, after re-derive) | **act** | shipped — fixed silent staleness bug in the usage rung + verdict |
| Behavioral consolidation | Det | **eager** (every tick, after attribution, before usage-refresh) | **act** | shipped — brain becomes durable accumulator; raw logs become disposable WAL |
| Usage refresh | Det | **eager** (every tick) | **act** | shipped — "more use → the right memories rise" live, not only at restart |
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
   dreaming) + held-out edge-prediction for connection discovery. Payoff is long-horizon; the
   single-operator signal is thin — an accepted limit (single-operator by design, foundation.md Decision 2).
4. **Novel framing** (vs. conventional consolidation = monolithic LLM rewrite): an offline **DAG of
   mostly-deterministic, individually-gated, removable passes** over an immutable log, **LLM passes
   fenced to propose-only**, **cadence from the det/LLM split**, **safe-to-rerun**. The *discipline* is
   the contribution, not the individual passes.

## Pass: fate-based credibility — the recorded-outcome / Tier-2 producer (designed 2026-05-30)

> **Re-aimed 2026-06-15.** The credibility/learning track's target signal has moved from code
> outcomes (did committed work survive / tests pass) to **relevance credibility** (which memories are
> *current / used / important / well-connected*). The outcome loop below — churn /
> proxy↔truth-on-commits — is **PARKED**: built, gated, dormant; a design record. **Sharpened
> 2026-06-23 — parked by nature, likely permanently:** clean *terminal* negatives are intrinsically
> scarce (competent fix-forward work resolves its failures), so they don't accrue regardless of
> volume/devs/capture; even instrumented coding is unlikely to resurrect this. The **firewall is
> unchanged**. Five dreaming actors shipped under this direction:
> **`AttributionRefreshPass`** — re-derives footprint usage attribution from the live graph + logs
> each tick and swaps it into the `AttributedSignalsRef` holder + rewrites `usage_attributed.jsonl`
> (fixed a silent month-scale staleness bug; see dedicated section below);
> **`BehavioralConsolidationPass`** — folds the log WAL's used-session sets into the durable
> behavioral store each tick so the brain becomes the system of record for its own usage (Architecture
> B; see dedicated section below);
> **`UsageRefreshPass`** — reads usage weights *from the behavioral store* (not the log files) and
> swaps them into the live `UsageWeightsRef` holder (L-R1 `UsageWeightedRetriever`);
> **`CoChangeRefreshPass`** — rebuilds the co-change index from recent commits, updates the `plan`
> tool's coupling data mid-serve; **`CentralityRefreshPass`** — recomputes structural-centrality
> weights from the live graph + cross-links, updates the L-R2 global `StructuralCentralityRetriever`.
> All are deterministic actors (may act; signal is external/structural fact, never model prose).
> Authoritative plan: [`learning-loop.md`](learning-loop.md).

The pass that produces the **"accumulated outcomes"** the belief-reconciliation node consumes, and the
**negative + graded Tier-2 signal** the proxy↔truth monitor (OLR §13.12/§13.20) has been starved of.
Designed in a working session against the live sample-project + this-repo brains; **supersedes the narrow
"detect git reverts / ingest CI" framing** of R3.

**The problem it solves.** Tier-2 capture was stuck: reverts ~never happen in a fix-forward workflow,
CI fires only on PRs (not normal commits), and a disciplined dev's *terminal* outcomes are
almost-all-positive **by construction** → binary pass/fail Tier-2 is structurally saturated, no
negatives, so proxy↔truth can't discriminate. The negatives don't live in terminal test results; they
live in the **fate of work over time**.

**The core move — read a memory's *fate*, not its *words*.** "Learn from itself" done safely is NOT
parsing memory text for good/bad sentiment (sycophantic, brittle, needs a hand-maintained keyword list
— and is the self-validation trap the moment the model's *opinion* becomes its own label). It
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
write *parallel* contradicting memories instead of `supersedes=` (the sample project does this → churn/revert/
reuse carry more weight there); footprint-less memories (a preference, a non-code decision) lean on
longevity + recurring-usefulness only; churn is genuinely noisy (active dev churns code for good
reasons) → keep it weak, prefer change-*reversing* churn.

**Build sequence.** (1) pure fate primitives + combiner (no I/O, gated); (2) wire the fate sources
(supersession index, git, attribution map, usage log) + emit the derived credibility/outcome view;
(3) the `DreamingPass` + scheduler slot; (4) verdict reads session-fate Tier-2. **Validate on our own
brain first (dogfood), then a deliberate sample-project serve restart** (the pass runs inside the
long-running serve, so the sample project only picks it up on restart). *(Done; the early text-outcome parser
was backed out in step (1) — see the core-move note.)*

### Credibility: automatic vs manual, and the A/B/C roadmap (clarified 2026-05-31)

A long session got confused here, so, plainly:

- **"Dreaming" = the automatic interval process** — the consolidation phase of the
  `MaintenanceTicker` inside the long-running serve (every `--dream-tick-minutes` + on each write).
  **`thalamus dream` is a manual dev/inspection tool** that runs one cycle and exits; it is *not*
  "dreaming" and does not count as the feature.
- **Perception ≠ consolidation (capture is a sibling phase, not a dreaming pass).** The same warm
  background clock (`MaintenanceTicker`) runs *two* phases per periodic tick: **perceive** — poll the
  code repo's git history into Brain 1 episodes (`--capture-tick`, warm in-process so there is no
  per-commit BGE cold start — the durable replacement for an external post-commit→sync hook) — *then*
  **consolidate** (dreaming), so a tick links and credibility-scores what it just captured. Capture is
  deliberately **not** a `DreamingPass`: a pass takes a read-only `PassContext` and writes only
  regenerable derived views (the §14.3 firewall), whereas capture writes raw, source-of-truth episodes
  from an *external* git source and sits *upstream* of the dream DAG. Same clock, distinct contracts;
  a write-trigger consolidates only (no git poll). `health --code-root` reports commits-behind-HEAD so
  a stalled capture can't hide.
- **Housekeeping ≠ consolidation (log rotation is a sibling phase, not a dreaming pass).** A third
  sibling phase runs on the same `MaintenanceTicker` *before* capture and consolidation: **housekeep**
  — rotate each append-only log (`retrieval.jsonl`, `usage.jsonl`, `trajectory.jsonl`, `dream.jsonl`)
  to numbered archive segments once it exceeds `--log-max-bytes`, keeping `--log-keep` segments.
  Readers concatenate the segments, so retained history stays whole; the bound caps unbounded log growth
  while Architecture B (below) makes the raw logs disposable. Housekeeping is deliberately **not** a
  `DreamingPass` for the same structural reason as capture: it writes the *filesystem* (rotates files
  that are sources of truth for the consolidation passes), whereas a `DreamingPass` writes only
  regenerable derived views. The contract is: a `DreamingPass` can always be safely re-run over the
  raw material; housekeeping changes the raw material itself. Failure-isolated — a rotation hiccup
  never skips perceive or consolidate. Write-triggers skip housekeeping (a `remember` call changed
  neither the logs' size nor the git history).
- **Fate = credibility = one engine** (`assess_fate` over superseded / reverted / reused / survived
  / churn). Two consumers: per-memory **credibility** (the `CredibilityPass`, a dreaming actor) and
  per-session **fate → Tier-2** (in the `verdict` report — a manual read-only *measurement*, not
  dreaming).
- The automatic dreaming loop runs each maintenance tick in the order below (see "As-built pass
  order" above for the full sequence): **structural-rederive** (re-derive Brain 2 from current source
  — hash-gated, runs first so new code nodes exist before re-linking), **structural-refresh**
  (incremental episode re-linking), **link-resolution**, **attribution-refresh**
  (`AttributionRefreshPass` — re-derives footprint usage attribution from the live gateway graph +
  the logs, swaps it into the in-memory holder the usage rung reads, and rewrites
  `usage_attributed.jsonl` for offline tools; runs *after* re-derive/re-link, *before* usage-refresh
  which consumes it), **behavioral-consolidation** (`BehavioralConsolidationPass` — folds the log
  WAL's used-session sets into the durable behavioral store via idempotent set-union; runs *after*
  attribution-refresh, *before* usage-refresh reads the store), **usage-refresh** (`UsageRefreshPass`
  — reads usage weights *from the behavioral store*, not from a file scan, and swaps them into the
  usage rung), **centrality-refresh** (`CentralityRefreshPass` — recomputes per-memory
  structural-centrality weights from the live graph + cross-links), **co-change-refresh**
  (`CoChangeRefreshPass` — rebuilds the co-change index from recent commits; updates the `plan`
  tool's coupling data), **credibility**, **belief-audit**. The corpora are declared per project (a
  `thalamus.toml` `[[corpus]]` array: `python-ast` / `scip` / `docs` / `text` / `findings`), so
  Brain 2 is not bespoke to one language; a corpus' `regen_command` lets the pass rebuild an external
  index (e.g. a `.scip`) when its source changes.

**The roadmap — three steps; only A is done, C is the goal and is gated:**

- **A ✓ — credibility computed in the automatic loop.** The `CredibilityPass` runs on the
  `MaintenanceTicker`'s consolidation phase, so the brain continuously scores each memory's fate.
  **Inert on its own** — nothing acts on the score yet (no recall change).
> **⛔ B and C are PARKED BY NATURE (2026-06-23) — a design record, not a live sequence.** Both need
> clean terminal negatives to discriminate, and those are intrinsically scarce in fix-forward work, so
> the monitor stays starved regardless of volume. What shipped instead is **relevance** credibility:
> recall already re-ranks by usage / centrality / supersession / recency (the L-R1/L-R2 rungs), the
> feedable form of "the brain reorganizes itself." Read B/C below as the *outcome-credibility* design
> that would apply only if negatives ever became plentiful (a very different workflow).

- **B — un-blind the proxy↔truth monitor.** Restart the serve (the supersede fix then lets
  supersessions actually *record* → fate **negatives** accrue) + accrue use → the monitor gets real,
  *discriminating* data (today it's starved: few units, ~all positive). *(Parked by nature — see above.)*
- **C — recall re-ranks by credibility (the goal: the brain reorganizes itself).** Recall surfaces
  high-credibility memories and demotes low-credibility ones. **Gated on B** — you do not deploy a
  feedback layer you cannot measure (§13.12: "if starved, *don't deploy a learned layer* — flying
  blind is the failure" — the self-validation trap). When B gives the monitor eyes, turn C on *measured*
  (A/B recall with vs without), **conservatively — never suppress a memory that is merely NEW**
  (unknown fate), only confirmed-bad.

Doing **C before B is the one thing not to do.** A is safe and done; B is enabled by the deliberate
serve restart; C is the payoff, on the far side of a working monitor.

## Pass: attribution refresh — keeping the Tier-1 signal live (shipped)

`AttributionRefreshPass` (`packages/dreaming/src/thalamus/dreaming/attribution_refresh.py`) is the
dreaming actor that keeps footprint usage attribution fresh during a long-running serve.

**The bug it fixed.** Attribution — which surfaced memories a session's committed work drew on —
is a re-derivable view of the recall logs + commit trajectory + the live code graph
(`FootprintAttributor`). Before this pass it was computed only by an offline CLI command, so a long
serve read a frozen attribution snapshot. After a month of commits and recalls, the snapshot was a
month stale, silently dragging down both the usage-weighted recall rung (which consumes the
attribution to compute weights) and the `verdict` report (which reads the same derived signal).
There was no error — the stale data was simply wrong.

**What it does.** Each maintenance tick, `AttributionRefreshPass` calls the injected `recompute`
seam (which re-runs `FootprintAttributor` over the live gateway graph + the current logs) and then
calls the injected `apply` seam (which swaps the result into the in-memory `AttributedSignalsRef`
holder that the usage rung reads, and rewrites `usage_attributed.jsonl` for the offline verdict and
rung-eval tools). Both seams are injected at the composition root (`serve.py`); the `dreaming`
package stays pure orchestration and never imports the attributor or log paths.

**Why it reuses `gateway.graph`, not a flat-config re-derive.** The composition root already holds
the live `StructuralGraph` inside the gateway. The declarative `[[corpus]]` path (a
`thalamus.toml` array) intentionally omits `code_language` from the per-corpus config, so any
attempt to re-derive a graph from the flat `ServeConfig.code_language` field would mis-language
the graph — the same silent misconfiguration that once emptied the co-change index. Using the
gateway's own graph avoids that class of bug entirely: the re-derive and attribution always see
the same graph.

**Firewall.** Deterministic over immutable logs; the signal is a behavioral act (the session's
committed work overlapping a memory's code footprint), never the model grading its own memory text.
May *act* (§14.3). DAG position: after `structural-rederive` + `structural-refresh` (needs the
current graph), before `behavioral-consolidation` + `usage-refresh` (both consume it).

## Pass: behavioral consolidation + the behavioral store — Architecture B (shipped)

`BehavioralConsolidationPass` (`packages/dreaming/src/thalamus/dreaming/behavioral_consolidation.py`)
folds the log write-ahead buffer's behavioral usage into the brain's durable store each maintenance
tick. It is the dreaming side of Architecture B: the brain becomes the durable accumulator of its
own usage history.

**The problem with the file-scan baseline.** Before Architecture B, `UsageRefreshPass` recomputed
usage weights by scanning the raw log files each tick. The raw logs were the system of record.
That is fine when the logs are short — and it is the correct *baseline* — but it is not durable:
if logs are rotated to cap unbounded growth, the discarded segments take their signal with them.
The eventual solution is for the brain to *accumulate* the signal so the logs can be safely
discarded after they are folded.

**What Architecture B is.** A `BehavioralStore` protocol
(`packages/experiential/src/thalamus/experiential/behavioral.py`) with two operations:
`record_usage(updates)` (union newly-observed used-session sets into the durable record —
idempotent) and `usage_weights()` (return per-memory usage weight = count of distinct sessions the
memory was recalled-and-used in — the same quantity the baseline computed from the file scan, now
read from the brain). Two implementations: `InMemoryBehavioralStore` (the boring baseline, for
tests and cold brains — not durable across a restart) and `Neo4jBehavioralStore`
(`packages/experiential/src/thalamus/experiential/neo4j_behavioral.py` — persists one
`M_behavioral_use` node per `(memory_id, session_id)` used-pair in the shared graph; `MERGE`-d so
re-folding the same logs is a no-op at the database level; no read-modify-write race). In a live
Neo4j serve, the durable store is used; in investigate mode, `InMemoryBehavioralStore` is used
(no behavioral writes, no persisted-usage bias).

**The consolidation primitive.** `consolidate_usage(store, events, signals)` in `behavioral.py`
derives the used-session mapping from a slice of the retrieval-event and usage-signal logs and
unions it into the store. The consolidation pass injects this as a callable seam, so `dreaming`
never imports the store or log paths.

**Why idempotent set-union makes the logs disposable.** Because union is idempotent, re-folding a
subset of the logs (e.g. after rotation drops old segments) neither double-counts nor loses prior
signal. The store accumulates; the logs only carry what has not yet been folded. That is what
makes the raw logs a disposable write-ahead buffer rather than the system of record — and no cursor
is needed for correctness (a cursor is only a later efficiency optimization, not a safety
requirement).

**Effect on `UsageRefreshPass`.** `UsageRefreshPass` now reads its weights by calling
`behavioral_store.usage_weights()` rather than scanning the log files. In a Neo4j serve the data
comes from the graph; in an in-memory serve it re-accumulates from the logs each startup. The rung
implementation is unchanged; the recompute seam injected at the composition root switched.

**Firewall.** Deterministic over immutable logs; the signal is a behavioral act (a session recalled
and used a memory), never the model grading its own prose. May *act* (§14.3). DAG position: after
`attribution-refresh` (folds the freshly re-derived attribution into the store alongside the direct
usage signals), before `usage-refresh` (reads the store).

## Sibling phase: housekeeping — log rotation (shipped)

The `MaintenanceTicker` (`packages/dreaming/src/thalamus/dreaming/runtime.py`) now runs a third
sibling phase, **housekeeping**, before capture and consolidation on each *periodic* tick
(write-triggers skip it). Housekeeping calls the injected `_rotate_logs()` closure (wired in
`serve.py`), which calls `rotate_log` on each unbounded append-only log (`retrieval.jsonl`,
`usage.jsonl`, `trajectory.jsonl`, `dream.jsonl`) — rotating it to a numbered archive segment
when it exceeds `--log-max-bytes`, keeping `--log-keep` segments and dropping older ones.
The derived `usage_attributed.jsonl` is *exempt*: it is a full-overwrite derived view, not an
append log, so it does not grow unboundedly.

Readers concatenate the archive segments, so the retained history stays whole. The ceiling is
roughly `log_max_bytes × log_keep` per log (default ~64 MiB × 8 = 0.5 GiB/log), which caps
unbounded growth until Architecture B (above) makes the raw retrieval/usage logs truly disposable
(they can be dropped after being folded into the behavioral store without losing signal).

**Why housekeeping is not a `DreamingPass`.** This is the same principle as "Perception ≠
consolidation": a `DreamingPass` takes a read-only `PassContext` and writes only *regenerable
derived views* over the immutable raw log (§14.3 firewall). Housekeeping *rotates the raw log
itself* — it changes the material that the consolidation passes operate on. If a housekeeping step
failed and corrupted a log, you could not fix it by re-running the pass DAG (the raw material would
already be damaged). A `DreamingPass` failure, by contrast, is always safe to re-run. The two
contracts are irreconcilable; the sibling-phase structure enforces the boundary at the level of the
`MaintenanceTicker` API (a plain `Callable`, not a `DreamingPass`). Failure-isolated: a rotation
hiccup never skips perceive or consolidate on the same tick.

## Open questions remaining

- Damping/hysteresis design for cross-cycle stability (hard part #2).
- Concrete eval of consolidation at single-operator scale (hard part #3).
- The reflection pass's exact gate and representation (semantic-principle node schema).
- Interaction with belief credibility decay (does decay touch beliefs, or only episodes?).
- Fate-combiner thresholds (survival horizon, churn ratio, reuse count) — tune on the settled backlog;
  report the verdict with/without each tier to see which fate signals actually carry discrimination.
- Session-work fate vs. memory credibility — same primitive at two granularities; confirm the
  session-footprint → per-session Tier-2 mapping for proxy↔truth.
- Calibrating the model-text tier against objective fate (does "looks-wrong" coincide with reverted/
  churned?) — the antidote to sycophantic self-judgement; needs enough overlap to measure.
