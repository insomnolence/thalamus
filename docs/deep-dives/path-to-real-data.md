# Deep dive — The path to real data (experiential ingestion & dogfooding)

*Part of [Project Thalamus design notes](../design-notes.md). Started 2026-05-25. `§13.x` →
[`outcome-learned-retrieval.md`](outcome-learned-retrieval.md); §1–§15 → [`../design-notes.md`](../design-notes.md).*

## Why this document exists

A recurring, honest question: **are we still building the framework, or are we testing the brain for
real?** The two had blurred. We have a working, tested, green system — and yet Brain 1 holds no real
data, and we have never observed the brain helping anything. This document names where we actually are,
why Brain 1 is empty, what "real data" even means, and the concrete path from here to a defensible
*"does it help?"* — so future sessions don't mistake a complete-looking framework for a validated one.

It also answers the two questions that prompted it: **"what is the next bullet?"** → the experiential
**ingestion spine** (below), which is the unfinished half of §10 step 1. And **"are we ready to test for
real?"** → not yet for the *verdict*; but we are ready to start *generating* real data, and that is the
next move.

## Where we actually are: instruments built, nothing measured

Everything green to date — `core` protocols, the encoders, the stores (incl. real Neo4j), `L0Retriever`,
the full §13.11 logging contract, the gateway, the structural hemisphere v0, the eval harness including
`utility@k` — is **plumbing plus measuring instruments, validated on hand-seeded or synthetic data.** The
tests prove the pipes carry data correctly and the rulers read correctly. **They prove nothing about
usefulness**, because nothing has used the brain.

This is the *intended* order (§10 step 0 = "instrument first", before the baseline, so the baseline is
measured from day one). But it means the green checkmarks are a statement about correctness of
construction, not about value delivered. We have built the rulers; we have not yet measured anything real
with them. Conflating the two is precisely the self-congratulatory failure mode §13.20 warns about (the
predecessor project's "it's learning!" story was a *metric* reading well on data that didn't mean what it seemed).

## Two senses of "data in Brain 1" (they unlock different tests)

"Get real data into Brain 1" is ambiguous; the two readings cost very differently and validate different
claims:

| Sense | What it is | Needs | Lets us test |
|---|---|---|---|
| **Curated knowledge** | hand-authored gotchas/decisions/preferences about *this* repo ("the async teardown is flaky", "we use uv workspaces") | someone to write a handful + an actuator that reads them | does *surfacing the right memory help the agent now* (anecdotal L2) |
| **Episodes from use** | what we did and *why*, with outcomes — captured, not authored | the **ingestion spine** + **real usage over time** | the §13 program: outcome-learned retrieval, "more use → more useful" |

The first is cheap and immediately useful for the *product* (retrieval that helps an editor today). The
second is the entire research thesis and is genuinely far off. We have been building toward the second
while holding **zero of either**. Both are worth pursuing; conflating them hides how far the thesis is.

## Why Brain 1 is empty — two distinct gaps

1. **No ingestion path (a framework gap).** The `experiential` package is a docstring. The trajectory log
   (§13.11b) faithfully captures the raw material — `COMMIT` with changed-file footprint, `TEST_RUN`,
   `ERROR`, `EDIT`/`REVERT` — but **nothing consolidates those events into episode records** that get
   stored and become retrievable. Today, episodes exist only when a test or demo hand-builds a
   `MemoryRecord`.
2. **No real activity to ingest (a usage gap).** Even with an ingestion path, episodes only appear if an
   actuator does real work *through the gateway* with the observers running. Nothing has. The §10
   discipline is "ship the boring version, **use it daily**, log obsessively" — we did the first and the
   third-in-principle, and skipped *use it*.

These are independent. Closing (1) without (2) gives an empty pipe; closing (2) without (1) drops real
activity on the floor. The next arc closes both.

## The data-readiness ladder

Where each rung sits, what it unlocks, and which eval level (§13.20: L1 proxy / L2 task-outcome / L3
ablation) it serves:

- **R0 — Instruments & plumbing. _(done)_** Substrate, retrieval path, logging contract, eval incl.
  `utility@k` — all validated on synthetic/hand-seeded data. Eval: **L1 on synthetic labels.** Proves
  construction, not value.
- **R1 — Curated Brain 1 + an actuator reading it.** Seed a few real, hand-authored memories about this
  repo; drive `recall` from a real actuator. First honest observation that *surfacing helps*. Eval:
  **anecdotal L2.** Cheap; does **not** need the spine.
- **R2 — Ingestion spine + dogfood. _(the next build)_** Segment the trajectory log into episodes and
  materialize them as Brain-1 records (a re-runnable *derived view*, §14.1); run the observers over our
  own development. Real episodes accumulate from use. Eval: **`utility@k` on genuinely real signals**;
  also yields the hindsight-relabeling dataset (§13.10) and footprint auto-linking (§13.19).
- **R3 — Tier-2 outcome capture.** Join terminal outcomes (commit kept, test fail→pass) to episodes →
  the truth signal + the proxy↔truth divergence monitor (§13.12). Eval: **L2 truth.** Gates learned
  layers honestly.
- **R4 — The learning thesis.** "More use → more useful." The *outcome-weighted* form (→ bent geometry
  §13.4) is **parked by nature** (terminal negatives are intrinsically scarce; see ROADMAP Track L).
  The live form is **relevance credibility** (shipped); M-1a remains only a non-reproducible mechanism
  pilot, and no replacement uncurated harness is active while normal-use telemetry accrues. The brain
  is **single-operator by design** ([`foundation.md`](foundation.md)
  Decision 2) — thin volume is an accepted limit, not a scale-up trigger.

So: **ready to start generating real data (R1/R2); not ready to render the verdict (R3/R4).** The verdict
cannot be faked or hand-built into existence — it requires accumulated real use, by design.

## The experiential ingestion spine (closing the framework gap)

The unfinished half of §10 step 1 ("capture the *why*"). It turns the raw trajectory log into Brain-1
episode records, behind a clean seam, deterministically, as a view that can be recomputed (so dreaming
can re-segment later — §13.16's "segmentation is a derived view, not a live decision", and §14.1's
capture-raw-derive-views).

- **Segmenter seam.** A protocol consuming `TrajectoryEvent`s and emitting episode spans. First
  implementations are the deterministic §13.16 boundaries: **S0 (request-bounded)** and **S1
  (commit-bounded)** — no learned judgment, no LLM. S2 (footprint discontinuity) and S3 (LLM) are later,
  behind the same seam, run only in dreaming.
- **Episode materialization.** From a span, build an episode `MemoryRecord` (`kind="episode"`) carrying:
  the span's event references (link back to the §13.11a retrieval events and §13.11b trajectory events),
  the **footprint** (changed files/symbols from commits — the §13.19 link anchor), the **terminal
  outcome** stub (commit / test fail→pass), and an **evidenced-why skeleton** (goal from the cue,
  rejected-alternatives from failed `TEST_RUN`/dead-ends), every why-component **tagged evidenced vs.
  asserted** per §13.17. No confabulated narrative; what we can ground, only.
- **Derived-view discipline.** The spine is idempotent and re-runnable over the raw logs; episodes are
  not the irreversible artifact (the logs are). This keeps segmentation a low-stakes modeling choice, not
  a silent poison (§13.16).

This populates Brain 1 from real activity and is the subject that auto-linking links to code and that
Tier-2 attribution attaches outcomes to. It is the keystone the R3/R4 rungs stand on.

## Dogfooding Thalamus on itself (closing the usage gap)

The cheapest real corpus is **Thalamus's own development**. This is not a stopgap — it is a genuinely good
first arena, and it is available now with no external agent:

- **Brain 2** ingests this repo's own AST (the `PythonAstIngestor` already does this for any Python tree).
- **Brain 1** ingests *our* episodes: the commits we make, the test runs the observers capture, the cues
  we would send. This development session *is* the trajectory the §13.11b observers were built to record.
- **The loop closes on real work**: a real task → real cue → real recall → real edit/commit/test → real
  trajectory → real episode → next time, real retrieval. That is R1+R2 happening on live data.

Using the thing is also the fastest way to learn *what is actually missing* — far faster than building
more framework speculatively. The §10/anti-self-validation discipline ("use it daily") is satisfied by
dogfooding, and it converts the project from "framework" to "framework with real data flowing through it."

## Honest limits

- **Single-operator, low-traffic volume.** Dogfooding produces a trickle of clean outcomes — enough to
  *exercise and debug* the pipeline and compute directional `utility@k`, likely **not enough to validate
  an outcome-trained ranker** (which is parked by nature anyway). This is an **accepted** limit of a
  single-operator brain (by design — [`foundation.md`](foundation.md) Decision 2), not something more
  users would fix. The relevance signals accrue fine at this scale, but M-1a is only a mechanism
  pilot. Dogfood proves the machinery runs on real data; it does not, alone, prove the slope.
- **The double-role bias.** When we are both the authors of the brain *and* its users, we will
  unconsciously feed it what it handles well. Treat dogfood metrics as *plumbing validation and anecdote*,
  not as the L3 verdict; the §13.20 integrity layer (proxy↔truth divergence, etc.) still arbitrates, and a
  neutral benchmark / second user is the real check.
- **What dogfooding can and cannot do.** Can: surface real episodes, exercise segmentation/linking/Tier-2
  capture end-to-end on real logs, give the first honest *"surfacing helped"* anecdotes, and tell us what's
  broken. Cannot: deliver the "more use → more useful" slope cleanly (confounded — §13.20) or substitute
  for the volume the learned layers need.
- **The M-1 ablation is not the thesis test (2026-06-17, 3-expert panel, `retained:5023addb`).** The
  intuitive framing — build a "gotcha avoidance" ablation (brain-on vs brain-off on tasks the brain
  holds warnings for) — is **circular**: if the remembered memory *is* the warning, injecting it
  injects the answer; curating cases the brain holds conditions on the brain winning; a negative result
  is structurally blocked. The correct reframe is **M-1a — a conversion/delivery probe**: *conditional
  on the brain already holding the decisive memory, does surfacing it cause an objective behavior change
  above generic salience?* That is a narrow necessary-condition proof and regression guard for the
  §13.10 prohibitive-memory path, **not** the thesis. Hard gates (non-negotiable): a held-out
  negative-control set (brain has no relevant memory → brain-on must not win); a generic-salience arm;
  a content-ablation arm; memories must be **episodes/why** (reason-from, not warning-shaped — no
  leakage); **programmatic blind judging** (a code detector, not an LLM grading prose — firewall);
  **pre-registration** before any run; per-case stats with anytime-valid CI / Beta-Binomial. The
  pre-registration lives at `docs/eval/m1a_preregistration.md`.

  The **better primitive to build toward** is a within-task decision-point ablation: at real dogfood
  decision points, compare two next-actions (brain-context vs ablated/decoy) scored by an external
  check. Per-*decision* N is the biggest power lever; cases are uncurated (recovers external validity).
  M-1a is its curated special case. Task-replay (real tasks, tests-as-judge) is closer to true M-1 but
  thin and expensive here. **Per-recall IPS** is the right long-run ATE estimator but blocked on R-7
  (`propensity=1.0` made off-policy estimation undefined — no common support) and on stochastic-serving
  volume (which accrues slowly at single-operator scale). R-7 built the propensity-logging substrate
  (2026-06-17) and exploration is now on (ε=0.05, 2026-06-26); the IPS estimator is deferred on volume.

## Status & next

Current position on the ladder, in short: **R0 done; R2's ingestion spine is built and dogfooding is
active.** The `CommitBoundedSegmenter` + `EpisodeBuilder` + `ingest_episodes` (in the `experiential`
package) are live; real episodes accumulate from development activity. **Track I (2026-06-17)** has
additionally made the brain the durable accumulator of its own behavioral usage: `Neo4jBehavioralStore`
/ `BehavioralConsolidationPass` consolidate the log write-ahead buffer into the brain each maintenance
tick; the flat event logs are now a rotatable, disposable WAL rather than the system of record for
learning. The usage rung reads from the brain. **R-7 (2026-06-17)** built the off-policy propensity
substrate so that if/when stochastic serving begins, the IPS estimator has common support. Active
build direction: see ROADMAP.md (commit I-3, then M-1a per the pre-registration, then Track C
capability work that also feeds Track L with dogfood volume).
