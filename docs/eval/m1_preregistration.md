# M-1b — pre-registration protocol (git-replay accumulation study)

*v2, 2026-07-25. Written before any run and before the sampler is built. Pre-registration is a hard
gate (CLAUDE.md §4). **Freeze/commit this, and the code implementing §5–§6, before the first run.**
Companion to [`m1a_preregistration.md`](m1a_preregistration.md). Do not add/drop repos, points, or
change a statistic after seeing results.*

---

## 0. Why this protocol exists — two failures, both on the record

**M-1a (the curated delivery probe) was quoted beyond its licence.** Its own protocol said a green
M-1a "must never be quoted as the thesis verdict"; `m1a.md` said the headline required anytime-valid
CIs that are not implemented. Both held the line; the *summary layer* (STATUS/ROADMAP) did not. Two
concrete failures: (1) **the frozen case set was never committed and no longer exists on disk**, so
the n=12+6 run is not reproducible; (2) an interim look at n=3 was analysed with a *fixed-sample*
cluster bootstrap where the protocol required an anytime-valid statistic. (n=12 was *inside* the
pre-registered N=10–15 band, so this was a pilot completed to target, **not**
escalate-until-significant.) Remediation: §10.

**M-1b v1 (2026-07-25, superseded by this document) was worse, and it is the reason for v2.** It was
written specifically to prevent the M-1a failure, and adversarial review — which re-implemented its
sampler against three real repos (`httpx`, `click`, `requests`; 353 decision points, 1,100 coupled
symbols) — found it would have produced **a positive result guaranteed by its own scoring function**:

- **Its score was rigged.** `|named ∩ coupled| / |coupled|` — pure recall, no precision term, no
  output cap. A larger brief yields more names yields higher recall, with no relation to usefulness.
  68% of surviving points had exactly one coupled symbol, so one extra guess flipped 0.0 → 1.0.
- **It was circular.** `planner.py:615` `_add_cochange` already folds historical co-change into the
  blast radius. The brief *was* a co-change table and the oracle *was* co-change; the "accumulation"
  manipulation was "make the table bigger." v1 had removed the content-ablation arm on
  anti-circularity grounds, deleting the only arm that would have caught this.
- **Its contrast measured code churn.** 63% of coupled symbols lived in files that did not exist at
  the early cutoff. `full_early` cannot name a symbol in a file it has never seen.
- **Its leakage filter selected against the signal.** Structurally-visible coupling fell from 43% to
  28% after filtering — it kept the hardest cases and would have produced a null for reasons
  unrelated to the thesis.

**The lesson v2 is built around:** a careful-sounding protocol that has not been attacked is not a
protocol. §12 therefore makes *the code* the frozen artifact, not the prose — v1's leakage rule
admitted a 3.2× swing in n depending on which defensible reading was implemented.

What survived review unchanged and is preserved below: the pre-committed licence (§2), the M-1a
remediation (§10), the deterministic code-only oracle, the negative control at mismatched scope, the
adversarial set, and **the core insight that git supplies decision points with ground truth
attached**. The idea was sound; the operationalisation was broken.

---

## 1. Two stages, and why they are separate

v1's deepest error was fusing two estimands into one contrast and reporting the fused result as the
accumulation slope:

- **E1 — prediction.** Does the brain's prediction of a change's reach improve as it accumulates
  history?
- **E2 — delivery.** Does an actuator handed that prediction produce a better action?

These are separable, **E1 is nearly free to measure**, and separating them is the only way a positive
is interpretable. Accordingly:

| | Stage | Actuator | Cost | Gate |
|---|---|---|---|---|
| **S1** | Deterministic accumulation sweep | none | hours | — |
| **S2** | Actuator delivery study | yes | ~3,000 calls | **runs only if S1 is positive** |

If S1's slope is ≤ 0, no actuator layer can rescue it, and S2 does not run. That single gate
converts a ~180-hour, ~$3,400 commitment into a one-day falsification test.

---

## 2. What a result licenses — PRE-COMMITTED, BINDING

Written before any number exists. Binding on every downstream document; a result quoted past this
section is a protocol violation regardless of the number.

### A positive **S1** licenses:

> On decision points mined from real git history across N public repositories, the structural blast
> radius computed by the `plan` tool identifies the cross-cutting reach of a change more completely
> as the repository history available to it grows (slope β, repo-clustered CI) — **and does so above
> a plain co-change lookup table built from the same history** (§4.2).

### A positive **S2** additionally licenses:

> An actuator handed that brief names the coupled symbols more accurately than with no brief, than
> with a length-matched placebo, and than with a salience nudge.

### Neither licenses — and this is the section that matters most:

- **"First-person experiential memory is validated."** Both stages measure a brain whose experiential
  content is **commit-derived** — post-hoc, sanitised subject lines — not the in-flight "why we did X,
  what we rejected" that the experiential hemisphere exists to hold. **Stage 1 does not touch the
  experiential hemisphere at all.** Arm F (§6.1) is the only component that speaks to first-person
  memory and is under-powered by construction.
- **"Thalamus makes developers more productive."** The oracle scores reach-anticipation, not task
  success, code quality, or time saved.
- **"The brain helps on tasks without a cross-cutting component."** Points are selected for
  multi-file reach; single-file changes are outside the sampled distribution.
- **Anything about the live serving brain's ranking rungs.** The replay brain is a reconstruction.

### A null licenses:

> Commit-derived history does not measurably improve reach-anticipation at this scale on these repos.

### A null does **NOT** license:

- **"The thesis is dead."** A proxy brain underperforming does not refute first-person memory. This is
  the symmetric error and is equally binding.

### Standing honesty requirement

The project has now built four instruments (M-2, M-4, impact-eval, M-1a) and designed a fifth, and
**every cheap, rigorous one lands on the structural hemisphere**. The experiential hemisphere — the
actual differentiator — remains unmeasured. Any public write-up of an S1 or S2 result **must state
this in the same paragraph as the result.** It is not a caveat to be relegated to a footnote.

---

## 3. The decision point (shared by both stages)

For a commit `C` in repo `R` touching symbols `S` spanning ≥2 files:

- **Entry point** `s₁` = the largest-hunk symbol **in a non-test file**. Points with no non-test file
  are dropped and counted. *(v1 took the largest hunk anywhere; measured, that landed in a test file
  in 58% of points — developers do not start in the test and predict the source.)*
- **Coupled set** = `S \ {s₁}`, **excluding symbols in the entry point's own file** (that coupling is
  handed over by the task cue and is not a prediction) and **excluding test-file symbols** (§7.3).
- **Task cue** = `C`'s subject line, leakage-filtered (§5.2), plus `s₁`'s file path.
- **Structural corpus** is built at `C^` (parent) and **frozen there for every arm and every
  accumulation level.** Only the *experiential* ingest window varies. *(v1 left this ambiguous;
  under the alternative reading, 63% of coupled symbols lived in files that did not yet exist at the
  early cutoff, making the contrast a churn-rate measurement.)*

---

## 4. Stage 1 — deterministic accumulation sweep

No actuator, no prompt, no LLM. Reuses `impact-eval`
(`packages/cli/src/thalamus/cli/impact_eval.py`), which already mines coupled pairs and scores
blast-radius recovery **with a temporal split** (`--cochange-commits` builds the index from commits
*older* than the test commits).

### 4.1 The manipulation

For each repo, at four history volumes q₁ < q₂ < q₃ < q₄ (quartiles of pre-`C` history by commit
count), build the co-change index and cross-link layer from that window only, holding the structural
corpus fixed at `C^`. Score every mined coupled pair. **Unit of analysis: the pair.** Thousands per
repo, which removes v1's power problem entirely (v1's MDE was ~0.06–0.09 against a base rate of
~0.2–0.4, on an effective n of 20 clusters).

### 4.2 The mandatory co-change-only baseline — the fix for the circularity

Because the `plan` brief *contains* a co-change rung, "the brain improved" and "the co-change table
got bigger" are confounded. v1 had no way to tell them apart. v2 makes the confound the control:

> At every quartile, score **two** predictors on the same pairs: **(i)** a plain co-change lookup
> table built from the same window, and **(ii)** the full blast radius (call graph + co-change +
> cross-links). Report **both slopes** and the **increment** (ii) − (i).

The headline is the increment, not the level. **If the full brain does not beat a ~50-line co-change
lookup, that is the finding**, and it must be reported as such. Reporting (ii) alone is a protocol
violation.

### 4.3 Scoring

Blast radius is a *set*, so recall alone is gameable by widening it. Report at a **fixed node
budget** (`cochange_max_nodes` and the radius budget frozen at their shipped defaults):
**recall, precision, and F1**, plus **hits@k** at the frozen budget. Report **mean radius size per
quartile** as a pre-registered quantity — if radius size is monotone in history volume, a monotone
score is not evidence (§8.3).

### 4.4 Stratification (pre-registered, not exploratory)

Score is reported **stratified by structural visibility at `C^`**: whether the coupled symbol is
reachable within 1 hop (same file, or the entry file imports the coupled module). Measured on the v1
sample, 57% of coupled symbols were invisible at 1 hop. *A brain that helps only on the invisible
stratum is the interesting result, and v1's design could not see it.*

### 4.5 Success criteria for S1 (pre-committed)

1. Increment slope β for (ii) − (i) > 0 with 90% repo-clustered CI excluding 0.
2. All three quartile increments ≥ 0 *(not "≥3 of 4" — see §8.3)*.
3. Mean radius size **not** monotone-increasing, **or** the F1 slope positive after conditioning on
   radius size. (If both score and radius grow together, the result is the artifact.)
4. The pair-level funnel is reported.

**S2 runs only if all four hold.**

---

## 5. Sampling and filtering (shared) — frozen as CODE, not prose

### 5.1 Repo selection

**N = 40 repos**, not 20. *(Rationale: the CI is cluster-limited; a percentile cluster bootstrap over
20 clusters undercovers, with ~30–40 the usual floor. More repos with fewer points each buys far more
power per unit cost.)* Mechanical criteria, no judgement, no post-hoc swaps:

- **Python only.** *(TypeScript is dropped: SCIP indexing requires `npm install` + a valid
  `tsconfig.json` at each historical checkout, which is unreliable across 18+ months of drifting
  lockfiles.)*
- ≥3,000 commits *(raised from 1,000: measured yield on repos at ~1,500–2,000 commits was only
  30–57 scoreable points, so the old floor would starve)*, ≥18 months history, ≥3 contributors;
- permissive licence (Apache-2.0 / MIT / BSD);
- not a Thalamus dependency;
- **all sampled commits `C` dated after the actuator's training cutoff** (§9, memorisation);
- selected by a seeded deterministic query over a public index, recorded in the manifest.

`docs/eval/m1b_repos.json` (URL + pinned SHA + licence + a **ranked reserve list**) is **committed
before the first run**. A repo yielding fewer than the target points is replaced by the next reserve
**before any arm executes**, and both appear in the funnel. This is the artifact M-1a lacked; its
absence is a run-blocker.

### 5.2 Leakage filter

Applied to the **commit subject only** — *not* the entry file path. *(v1 filtered against subject +
path, which killed 83% of points and, because any coupling inside the entry file is definitionally a
path "leak," selected against exactly the predictable cases: structurally-visible coupling fell 43% →
28%. Entry-path exposure is instead handled structurally, by excluding same-file coupling from the
oracle — §3.)*

A point is excluded if the subject contains a coupled file's basename or a coupled symbol's
identifier, case-insensitive, **after normalising both sides by stripping separators** (so
`test_queryparams` matches `QueryParams` — a real v1 survivor that leaked the answer in its path).

### 5.3 Exclusions, counted and reported by reason

Merges, reverts, dependency bumps, vendored/generated paths, >60% new-file additions, and
mechanical-sweep commits. **The sweep-exclusion list is frozen as a tested regex set**, not prose —
v1's prose let through "Treat warnings as errors", "Resolve typing errors", "fix mypy findings",
"Linting", and "Attempt to resolve test flakiness" (18% of survivors).

### 5.4 Coverage honesty

The full funnel is reported: candidates → excluded (by reason) → sampled → leak-filtered → scored.
Points surviving to an empty structural radius are reported as `starved`, counted, and **included as
misses**.

---

## 6. Stage 2 — actuator delivery study (gated on S1)

### 6.1 Arms

| Arm | Block |
|---|---|
| **A · `off`** | length-matched placebo |
| **B · `salience`** | `off` + content-free nudge |
| **C · `full_early`** | brief at q₁ |
| **D · `full_late`** | brief at q₄ |
| **P · `volume_placebo`** | **D's brief with its memories/nodes replaced by an equal volume drawn from the negative-control brain** |
| **F · `first_person`** | *dogfood repos only* — brief including real operator memories |

**Arm P is new in v2 and is the primary control.** v1 asserted all six arms were length-matched,
which is impossible for C vs D — differing content volume *is* the manipulation — leaving the
**primary** contrast as the only one with no length or salience control. D − P is the length-clean
accumulation contrast. **Per-arm prompt token counts are a pre-registered reported quantity.**

Arm E (`structural_only`) from v1 is dropped: with the corpus frozen at `C^` (§3), S1 already
isolates the structural contribution deterministically.

Arm F is reported **separately, with its own n**, and is explicitly under-powered.

### 6.2 Sampling for S2

A **stratified subsample, n ≈ 60–80**, drawn from points where **S1 showed the q₁ and q₄ briefs
actually differ** (no point in asking an actuator to convert a difference that isn't there),
stratified by the §4.4 visibility strata.

### 6.3 The envelope — frozen verbatim

v1 said "same envelope as M-1a." M-1a's envelope (`harness.py:20`) says *"output only the concrete
change or decision you would make"* — it elicits a code change, not an enumeration, so `named(output)`
would extract almost nothing in every arm. The M-1b envelope is:

```
You are a coding actuator. You are about to change {entry_point} in {entry_file}.

## Task
{task}

## Context
{block}

List at most 5 OTHER symbols this change must also touch, most likely first,
one per line, as `path::Symbol`. Output only the list.
```

### 6.4 Scoring — capped and precision-aware

`named(output)` parses the list, **truncates to K = 5**, and resolves entries to `StructuralRef`.
Score is **F1** against the coupled set, with precision@K and recall reported separately.

*(v1 scored uncapped recall, which the larger brief wins mechanically. Note also that v1's §5 claimed
`named(output)` resolves "through the same `map_changes_to_refs` path" — **this was false**: that
function takes diff hunks, not prose. The extractor is new code, and it is frozen pre-hoc under §12
because its precision moves every arm's score.)*

**Mean `|named(output)|` per arm is a pre-registered reported quantity.** If it differs materially
across C and D, the contrast is uninterpretable and must be reported as such.

### 6.5 Trials

4 per arm per point, temperatures `(0.2, 0.7)`, seeds `0..3`, fixed in advance. **Seed is honoured by
the Ollama and OpenAI backends only** (`actuator.py:98,185`); the Anthropic and Gemini backends accept
and ignore it (`:137,:216`) — so with 2 temperatures those runs are 2 configurations with repeats, not
4. Either pass the seed or drop the reproducibility claim for those backends; do not imply both.

**Per-arm error rates are reported, and any point where any arm errored is excluded.** `_act_resilient`
returns an error marker that fails the oracle; because arm D has the longest prompts, timeouts and
context overflows are differentially likelier there, so the "conservative miss" argument does not hold.

---

## 7. Controls

### 7.1 Negative control (MANDATORY)

Points whose brain is built from a **different repo's** history, ingested **under the target repo's
scope** so the store's exact-scope filter (`in_memory.py:117`) does not trivially return nothing.
*(v1's construction would have returned an empty brain, giving δ≈0 for the trivial reason that there
was nothing to retrieve — falsely passing the criterion.)* A canary asserts the control brain
actually returns memories. Expectation: **D ≈ A**. Runs at the **same n and same arms** as the
primary — it is the one control that catches the F1-class artifact, so it cannot be a smaller side
set.

### 7.2 Adversarial set

Points where the pre-`C` brain holds a belief `C` overturns (mechanically: `C` rewrites >70% of a
region a prior ingested commit introduced). Does the brief make the actuator **worse**? Reported
whatever it says.

### 7.3 Test symbols

Excluded from the oracle. `PlannerConfig.cochange_skip_tests` defaults to `True`
(`planner.py:361`), while 28.8% of measured coupled symbols live in test files — leaving them in
imposes a ~71% recall ceiling unrelated to accumulation, and one that varies by repo (inflating
cross-repo heterogeneity and hence the clustered CI). The run additionally reports the
`cochange_skip_tests=False` variant as a secondary, descriptive figure.

---

## 8. Statistics

### 8.1 Clustering

All CIs are **repo-clustered**: resample repos, then points, then trials. `stats.py:45` `bootstrap_ci`
currently resamples a flat list despite a docstring claiming otherwise — **implementing the clustered
bootstrap is a build-blocker**, alongside the e-process CI.

### 8.2 Anytime-valid CI

**Required for any headline, not an additive refinement.** This protocol anticipates interim looks as
repos complete. *Alternative permitted:* pre-commit to **one terminal look and no interim analysis**,
which makes a fixed-sample clustered bootstrap valid and removes this build — but that election must
be recorded here **before the first run**, and honoured.

### 8.3 Slope and monotonicity

β is fit against `log(commits ingested)` over four quartiles. The criterion is **all three increments
≥ 0**, not "≥3 of 4" — under exchangeability the latter reads most naturally as a **50% coin flip**
(the strict reading is 1/24). Because the four brains are *nested*, any monotone nuisance produces
monotonicity by construction, so monotonicity of **radius size** (S1) and of **|named(output)|** (S2)
is checked as a **negative** criterion. A permutation test on the quartile ordering is reported.

### 8.4 Sign test

Ties are dropped by `sign_test_p` (`stats.py:33`); with small coupled sets, δ = 0 will be common.
**The expected tie rate is a pre-registered reported quantity** so it is not discovered post hoc.

### 8.5 Multiplicity

§4.5 and §6 form all-must-hold conjunctions, which conservatively control type-I error for the
headlines. Every other number — per-repo δ, arm-F contrasts, secondary contrasts, stratum-level
scores — is **descriptive and uncorrected, and must be labelled so in the report itself.** Given §0's
history, **only the conjunctions are quotable.**

---

## 9. Validity threats

| Threat | Control |
|---|---|
| **Bigger brief wins mechanically** | Output cap K=5; F1 not recall; per-arm `|named|` reported; arm P (volume placebo); radius size as a negative criterion. |
| **Circularity — brief contains a co-change table, oracle is co-change** | The co-change-only baseline is a mandatory arm; the headline is the **increment** over it (§4.2). |
| **File-existence churn masquerading as accumulation** | Structural corpus frozen at `C^` for all arms (§3). |
| **Leak filter removes the signal** | Filter on subject only; same-file coupling excluded from the oracle; results stratified by structural visibility (§4.4). |
| **Answer in the entry path** | Separator-stripped identifier matching (§5.2); non-test entry points (§3). |
| **Actuator memorisation** | Sampled commits post-date the actuator's training cutoff (§5.1) — *the only control that actually works*. Pinning SHAs controls the corpus, not pretraining. Secondary: a matched-decoy contrast (does arm A name `C`'s coupled set above the rate it names a *different* same-repo commit's?). |
| **Temporal leakage** | **Provenance canary:** for every arm-D brief, assert no constituent artifact (memory, node anchor, co-change pair) carries a provenance sha with committer-date ≥ `C`. *(v1's "leak canary" — a post-`C` brain must score higher — cannot fail and detects only liveness; it is retained under that name.)* |
| **Single actuator** | Replication on a second, different-family actuator before any headline is quoted. |
| **Shared representational bias** | Disclosed: if corpus embedder and actuator share a model family, retrieval and consumption share biases. Not a firewall violation (no grading signal), but the replication should vary the embedder, not only the actuator. |

**Firewall:** not violated. The oracle is code, ground truth is git, no model grades any model's prose.

---

## 10. M-1a remediation (binding, do first)

The M-1a frozen case set does not exist on disk and cannot be committed. Therefore:

- **STATUS.md, ROADMAP.md, and README.md downgrade M-1a** from "POSITIVE, control-validated" to
  **"pilot; cases not preserved, not reproducible."** The prereg and `m1a.md` already carry correct
  language — only the summary layer needs correcting.
- The M-1a **mechanism** is validated and reused here; what is retracted is the **quotable result**.
- Any rerun commits the cases file **before** the run and implements the e-process CI **before** the
  headline.

---

## 11. Cost

- **S1:** deterministic, hours. This is the point of the two-stage split.
- **S2:** ~60–80 points × 6 arms × 4 trials ≈ 2,000 calls, ×2 for replication ≈ 4,000 (~$100–600
  depending on actuator class).
- **Brain builds are the binding constraint, not actuator calls.** v1 implied ~1,770 full builds
  (~180 h serial). v2 exploits nesting: the q₁⊂q₂⊂q₃⊂q₄ brains are monotone, so **walk each repo's
  timeline once, ingest incrementally, snapshot at quartiles**, and place all of a repo's points at a
  small number of **shared cutoffs**. This changes the estimand slightly — accumulation is measured at
  repo-level cutoffs, not per-point — and is pre-registered here for that reason.
- Episodes are pure per-commit functions (`episode.py:148` derives id `episode:{sha}` from one commit),
  so each commit is embedded **once per repo, ever**, and every quartile brain is a subset selection.

---

## 12. What must be frozen as CODE before the first run

v1's filters were prose, and adversarial re-implementation showed four defensible readings producing
a **3.2× swing in surviving n**. A pre-registration whose sample size depends on the implementer is
not frozen. The following ship as tested, committed code **before any run**, and are the frozen
artifact this document merely describes:

1. The decision-point sampler (entry point, coupled set, exclusions) — §3, §5.3.
2. The leakage filter, including separator-stripped matching — §5.2.
3. `named(output)` extraction and ref resolution — §6.4.
4. The repo manifest + reserve list — §5.1.
5. The clustered bootstrap and (unless §8.2's single-look election is made) the e-process CI.
6. The provenance canary — §9.

**Run-blockers, all of them.**

### Reusable, verified

`impact.py` `parse_changed_lines` / `map_changes_to_refs`; `impact_eval.py` git archaeology;
`brain.py` `build_code_graph` / `build_two_hemisphere_gateway` (in-memory, no Neo4j —
verified to compose end-to-end); `planner.py` `Planner.plan` / `PlanBrief.render`; the entire
`m1a/actuator.py` backend layer.

### Needs modification

`m1a/harness.py` is bool-only (`TrialRecord.hit: bool`, `hits/trials`) and must carry a continuous
score. `m1a/cases.py` hardcodes four arm names and rejects case files missing any of them.
`m1a/stats.py` hardcodes arm names and does not cluster.

### Safety requirement (non-negotiable)

The harness is **100% in-memory** and must never construct a `Neo4jStore`. Note there is **no
`conftest.py` in the repository** — Neo4j isolation is convention-only — and `cli/project.py:86-89`
bridges a `thalamus.toml` `neo4j_uri` into `os.environ` via `setdefault`, so a run started from a
directory containing a `thalamus.toml` can silently reach the live brain. The harness pops both URI
env vars at entry and asserts the store type.

---

## 13. Relationship to the single-operator decision

No position on, and no change to, `foundation.md` Decision 2. Replay supplies *history*, not *users*:
every brain built here is a single-operator brain reconstructed from one repo's past.
