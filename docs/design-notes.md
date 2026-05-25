# Thalamus — Design & Research Notes

**Version:** 0.1
**Started:** 2026-05-24
**Status:** design exploration, pre-implementation

This is a living document. It captures the goal, the architecture as currently conceived,
the honest assessment of what to build with vs. what to avoid, the relevant research, and
the open questions where real contribution is possible. It is intentionally opinionated and
honest — including about ideas that were considered and set aside.

---

## 1. The goal

A persistent brain for agentic LLMs that gives them what they fundamentally lack: memory,
recall, and accumulated understanding across sessions.

The motivating observation: LLMs are predictive and stateless. The current state of practice
— using framework files / scratchpads as "memory" — has three failures the user has seen in
practice:

1. They **forget** (relevant memory isn't surfaced when needed).
2. They are **never updated** (stored knowledge goes stale; contradictions accumulate).
3. They are **often useless or ignored** (low signal, not trusted, not integrated).

The better model is a real brain: experiences and knowledge that consolidate over time, get
revised when reality changes, prune what stops being useful, and surface the right thing from
a partial cue. The thesis: *the more the brain is used, the more useful it becomes* — via a
feedback loop, not a clever model.

---

## 2. Lessons carried in from Polynoica (read these first)

Thalamus is spun out of Polynoica, which tried to use a JEPA orchestrator as the reasoning
core and failed. The hard-won lessons:

- **Don't make an unproven exotic model the foundation.** Polynoica bet its core on a
  JEPA orchestrator that was never validated and could not be. Build the foundation from
  proven, boring parts; put novelty in a layer *above* it.
- **Every novel layer must be removable and measurable against a boring baseline.** If the
  novel layer can't beat the baseline, you find out in a week, not six months.
- **Reward/eval signals must come from OUTSIDE the model's own embedding space.** Polynoica's
  central failure was a self-referential reward (cosine distance between response and query,
  both through the same frozen projection) — it measured self-consistency, not quality, and
  produced a false "it's learning" narrative. Any learning signal here must be externally
  observable (e.g. "did the retrieved memory get used / did the task succeed").
- **Pick the right arena for the right tool.** JEPA / world models shine on continuous,
  perceptual, temporal data (vision, control). Symbolic code-agent memory is the wrong arena
  for them. Same category error applies to LNN/SNN (below).

---

## 3. What JEPA actually is, and why it was dropped

JEPA (Joint Embedding Predictive Architecture) is a **self-supervised representation-learning
method**: hide part of an input, predict the *embedding* of the hidden part (not raw
pixels/tokens), with the target from an EMA copy of the encoder. Proven home: I-JEPA / V-JEPA
learning visual features from unlabeled data.

Clarifications that mattered:

- **"World model"** is the *aspiration* built on top of JEPA — predict next latent state given
  an action, then plan by rolling it forward. Only works with a real environment (states +
  actions). JEPA ≠ world model automatically.
- **"Self-learning"** means *self-supervised* (no labels), **not** "autonomously gets smarter
  with use." The "gets better with use" we actually want is a **feedback loop**, a much simpler
  and more honest mechanism.

**Why dropped for Thalamus:** memory and retrieval are storage / indexing / ranking /
consolidation problems, not world-modeling problems. The only genuinely JEPA-shaped slot is
*predictive retrieval* ("predict the latent of the memory we'll need next"), and even that is a
late-stage optimization that must beat a usage-weighted baseline before it earns a place. It is
not a foundation. (See §9 for the one place JEPA could re-enter, gated.)

---

## 4. Architecture (current conception)

### Two hemispheres, kept separate

The split is empirically motivated: a single polluted store caused real problems in prior work.
The hemispheres also differ in *kind*, which justifies separation regardless of physical layout:

| | Brain 1 — Experiential | Brain 2 — Structural |
|---|---|---|
| Holds | what we did, *why*, preferences, history | structural graph of a re-derivable corpus (code first) |
| Nature | irreplaceable, append-mostly | deterministic, re-derivable by re-parsing |
| Update | consolidated/revised over time | re-synced on file change (watcher / git hook) |
| If lost | gone forever | just re-parse the repo |

(**Resolved** — see [`deep-dives/foundation.md`](deep-dives/foundation.md): what broke before was
*retrieval/index pollution*, not shared storage → **one graph substrate, two typed namespaces,
separate vector indexes per hemisphere, cross-hemisphere links as native edges.** Logical separation
of lifecycle/backup with no index mixing, without forfeiting native linking.)

(**Brain 2 is corpus-agnostic** — a re-derivable structural graph built by a pluggable `Ingestor`:
Python code first; other languages (tree-sitter / SCIP) and document corpora are future ingestors
behind the same seam, each with its own index. See
[`deep-dives/structural-hemisphere.md`](deep-dives/structural-hemisphere.md).)

### The gateway conduit

A single programmatic conduit (likely **MCP / FastMCP**) is the *only* interface the LLM agent
talks to. The agent never touches individual stores, encoders, or graphs. The conduit takes a
prompt + current focus, routes/queries both hemispheres, and returns a clean, machine-structured
context payload (target code, structural dependencies, constraints/"gotchas", style preferences).

This is a far better product surface than Polynoica ever had — it makes the brain usable with
off-the-shelf agentic editors immediately.

### The LLM is an actuator ("the scalpel")

The LLM is stripped of planning/memory overhead and works in a tight, hyper-focused window,
executing edits under constraints the brain supplies. This is the one part of the original
design with direct empirical support: in Polynoica, *graph-aware single-shot context* (Arm B)
was the only configuration that beat raw LLM (+0.21 to +0.26 on the high-benefit set).

### Classical routing layer

Prompt → vector (BGE-small) → fast intent classifier (XGBoost / linear), CPU, <10ms, no token
generation. A labeled classifier with ground truth beats learned prototypes for intent routing.

### "Gotcha" detection — use deterministic analysis, not latent geometry

The original spec proposed a Codebase JEPA detecting "geometric tension" to predict downstream
breakage. **Don't.** The example gotchas ("changing this signature breaks decompressor.py",
"this runs in an async loop, don't add blocking I/O") are derivable by walking the AST
dependency edges + signature/type info + a ruleset — deterministically and correctly. Static
analysis / type-checkers already do exactly this. A JEPA would be a fuzzy, hard-to-train
approximation of something computable exactly. Ground truth for "did this edit break something"
is `pytest` / `mypy`; a latent model can only ever distill that, worse.

### Saliency — use a labeled classifier, not prediction error

The original spec proposed using JEPA prediction error as a saliency score ("good morning" =
low error = discard; "switch to SQLite" = high error = keep). **Prediction error measures
novelty, not importance.** The 500th "switch the database" is low-error but still important;
a first-ever "good morning" is high-error but worthless. Saliency ≠ novelty. Use the BGE +
classifier layer to score "is this an actionable/architectural decision?" directly, with labels.
(A principled surprise-gated mechanism exists — see §6 Titans — but importance scoring is the
honest first cut.)

---

## 5. Why simple graph/vector retrieval is mediocre (failure modes)

The user has seen flat graph/vector RAG in practice and found it lacking. The diagnosable
reasons:

1. **Surface similarity ≠ relevance-to-task.** Embeddings encode "what is this about," not
   "is this useful right now."
2. **Write-once, never revised.** New experiences append; they never update or supersede old
   ones. Contradictions and staleness accumulate.
3. **No forgetting / no salience.** Flat, equal-weight storage. Noise drowns signal.
4. **No abstraction.** Stores 50 raw transcripts where a brain stores one principle ("this
   codebase has a flaky async teardown").
5. **Single-shot, context-blind.** One cosine lookup; no iteration, no multi-hop, no awareness
   of trajectory or what was already retrieved.
6. **Dumb context assembly.** Good hits stuffed into a prompt ≠ a usable understanding.
   (Polynoica re-learned this: packaging mattered as much as retrieval.)

Every better architecture below is a targeted fix to one or more of these.

---

## 6. The research map (architecture-level, brain-inspired, buildable)

These are the state-of-the-art lines for agent memory. Notice how many are explicitly
neuroscience-inspired — the "real brain" instinct is vindicated *at the architecture level*,
not the neuron level.

- **Generative Agents** (Park et al., 2023) — memory stream + **importance scoring** +
  retrieval by recency + importance + relevance + **reflection** (periodically synthesize
  higher-level insights and store *those*). Reflection *is* consolidation/"dreaming." Fixes
  modes 1, 3, 4.
- **MemGPT / Letta** (Packer et al., 2023) — OS/virtual-memory metaphor: small in-context "RAM"
  + large external "disk", LLM **pages and rewrites its own memory** via function calls. The
  source of "memory that updates itself." Fixes mode 2.
- **GraphRAG** (Microsoft, 2024) — LLM-built entity knowledge graph + community detection +
  community summaries → answers global/thematic questions flat RAG cannot ("what are the
  recurring architectural tensions in this repo?"). Fixes modes 4, 5.
- **HippoRAG** (Gutiérrez et al., 2024) — modeled on **hippocampal indexing theory**; builds a
  KG and runs **Personalized PageRank** for associative, single-step multi-hop retrieval.
  Closest thing to "find the *connected* memory, not just the *similar* one." Beats flat RAG on
  multi-hop. Fixes modes 1, 5.
- **RAPTOR** (Sarthi et al., 2024) — recursive clustering + summarization into a **tree** of
  increasing abstraction; retrieve at the right altitude. Fixes mode 4.
- **Complementary Learning Systems** (McClelland, O'Reilly) — fast hippocampal episodic store +
  slow neocortical semantic store + **offline replay** between them. This is the validated
  template for the two-hemisphere + dreaming design.
- **Titans** (Google, "Learning to Memorize at Test Time", late 2024) — neural memory that
  writes based on **surprise**, with momentum and learned forgetting. A *principled* version of
  the original saliency intuition. Promising, frontier, not battle-tested — study, don't found on it.

### How they compose into Thalamus

- **Brain 2 (structural)** = GraphRAG / HippoRAG-style code KG with multi-hop, structure-aware
  retrieval.
- **Brain 1 (experiential)** = Generative-Agents memory stream + importance + **reflection /
  consolidation**, made **self-editing** (MemGPT) so beliefs are revised, not just stacked.
- **Dreaming** = consolidation pass: reflect raw episodes → semantic principles; GNN / PageRank
  link-prediction to surface non-obvious connections; decay + prune by importance × recency ×
  usage; reconcile contradictions (LLM pass).

---

## 7. Substrate vs. architecture — where novelty actually pays

Two layers where one could innovate:

- **Substrate** = the kind of neuron/computation (JEPA, LNN, SNN, Mamba/SSM, transformer).
- **Architecture** = how the memory *system* is organized (store, index, rank, forget,
  consolidate, learn-to-retrieve).

**A memory brain's hard problems are all at the architecture layer; the exotic models people
reach for are all substrate.** Swapping the neuron type doesn't touch the bottleneck — like
putting an exotic engine in a boat when the problem was the hull.

- **Liquid Neural Networks** — great for streaming continuous-time signals with distribution
  shift (robotics, control, sensors). Memory recall is not a continuous-time dynamical system.
  Wrong arena.
- **Spiking Neural Networks** — payoff is energy efficiency on neuromorphic hardware. On a GPU
  you simulate spikes and lose the benefit, while paying harder training + immature tooling.
  No payoff on commodity hardware.

Principle: **build the foundation from proven, boring parts; put novelty in the layer above,
where the field is genuinely stuck — and make that layer measurable/removable.**

---

## 8. Modern Hopfield Networks — assessment

The most *legitimate* of the exotic options (it's an actual associative memory), but it belongs
as a **component, not the backbone**:

- **It reduces to attention.** Ramsauer et al. proved the Modern Hopfield update rule *is*
  softmax attention over stored patterns. So the LLM already does Hopfield-style associative
  recall internally (bounded by context), and a vector store does scaled/persistent retrieval
  more simply and inspectably. Hopfield-as-external-memory is squeezed between the two and tends
  to collapse into "attention over a vector bank."
- **Correlated memories → metastable/blended retrieval.** Its clean-retrieval / exponential-
  capacity results assume *well-separated* patterns. Real memories are correlated and
  overlapping → it settles into a smear of similar memories.
- **Lacks system-level properties.** No native update/revision, structure, provenance,
  forgetting, or inspectability — a dense parametric blob, not a queryable store. HippoRAG-style
  graph+PageRank retrieval already captures the *useful* associative-recall idea while keeping
  those properties.

**Where it genuinely could win:** *pattern completion from a partial/noisy cue* ("that thing we
did with the async teardown…") via attractor dynamics — the one thing plain top-k cosine doesn't
do. Also valid as a differentiable layer inside a trained model.

**Bounded experiment if pursued:** pit a Hopfield completion layer vs. plain retrieval on
partial/noisy queries; keep only if it recovers the right memory more often.

---

## 9. The open frontier (where real contribution is possible)

The research above is strong but each leaves gaps. The genuinely open, differentiated bets —
all *removable, measurable layers on a working base*:

1. **Belief revision, not just appending.** Most systems (even reflection) *add* memories; few
   truly **reconcile and supersede** ("preferred X in March, switched to Y in May, here's why").
   A memory that maintains current truth *and* its history is underbuilt. **§13.17 locates this in
   the *why layer*** — whys are mutable beliefs layered over immutable episode history.
2. **Retrieval that learns from real outcomes.** Close the loop with an *external* signal: "did
   the retrieved memory get cited / did the edit pass tests?" This is the exact idea that failed
   in Polynoica's orchestrator (self-referential there) but is *observable* in a memory system.
   **Polynoica has a parked branch — `wip/experience-weighted-retrieval` — that started this**
   (edge dedup, outcome tracking, outcome-aware ranking, GNN outcome features). Worth mining.
   **Worked through to depth in §13.**
3. **Cross-hemisphere linking.** Tie experiential "why we did X" to structural "where X lives in
   the code." Code-RAG ignores autobiographical memory; agent-memory work ignores code structure.
   Joining them is differentiated and largely undone. → **§13.19**: deterministic footprint links
   resolved by symbol-identity; resolution-failure = the §13.18-D2 staleness signal; enables
   structure-aware experiential retrieval complementary to §13.4.

**The one place JEPA could re-enter (gated):** *predictive retrieval* — learn a latent space
organized by *retrieval need* rather than surface semantics, and retrieve nearest to a predicted
"what we'll need next" vector. Requires logs from the usage-weighted baseline first, and must
beat that baseline. Late optimization, not foundation.

---

## 10. Proposed build order

Anti-Polynoica discipline: ship the boring working version, log obsessively, add learning layers
that train on *real* logs and are gated against the boring baseline. (§13 is the **map of gated
options, not a build mandate** — most of it stays unbuilt unless its gate opens.)

0. **Instrument first.** Eval harness + curated benchmark (§13.20), the §13.11 logging contract,
   deterministic cross-hemisphere links (§13.19), and **tenant/repo-scoped schema & logs** (foundation).
   Must exist *before* the baseline, so the baseline itself is measured from day one (can't gate rung-1
   against a rung-0 that was never instrumented).
1. **Measured baseline:** one graph / two namespaces / separate vector indexes (foundation) + plain
   semantic retrieval + capture the "why" + trajectory capture (§13.11b). Ship, use daily, measure
   against step 0. **(The "capture the why" + "use daily" half is the experiential ingestion spine +
   dogfooding — the unfinished part of this step; see [`deep-dives/path-to-real-data.md`](deep-dives/path-to-real-data.md).)**
2. **Log what gets used / what succeeds.** Usage/outcome-weighted retrieval = semi-bandit (§13.5) +
   hindsight relabeling (§13.10) on the frozen-BGE baseline (revive the parked Polynoica branch).
3. **Dreaming as a pipeline:** deterministic prune/merge + belief reconciliation (§13.18) +
   segmentation/why refinement (§13.16/§13.17) + GNN link-prediction for connection discovery +
   reflection-style abstraction.
4. **Frontier learned layers, each gated against the §10.2 baseline:** bent retrieval geometry
   (§13.4), outcome-weighted link credibility (§13.19), belief-contradiction D3, and (only if logs
   justify) predictive-retrieval JEPA.

---

## 11. Reusable assets from Polynoica

The parts that earned their keep map almost one-to-one onto the structural hemisphere + routing.
(Verify against current Polynoica code before relying — paths drift.)

| Thalamus need | Polynoica asset |
|---|---|
| AST → code graph | `packages/memory/.../knowledge/code_ingestor.py` (AST ingest w/ `source_path`/`line_start`/`line_end`) |
| Dependency-tree queries | `Neo4jKnowledgeGraph` + `query_subgraph()` (BFS/Cypher k-hop) |
| Context payload (code blocks + relations + excerpts) | graph-context renderer (`GRAPH RELATIONSHIPS:` / `SOURCE EXCERPTS:`, line-numbered) — weeks of tuning (aliases, excerpt ranking) transfer |
| Prompt → symbol routing | code-search keyword/alias expansion (`before_request → preprocess_request`, …) |
| Classical routing (BGE + classifier) | `SentenceEncoder` (BGE wrapper) + `PrototypeDispatcher` (or swap a labeled XGBoost) |
| Vector store | `InMemoryStore`, `Neo4jStore` (native vector index) |
| Recency/frequency weighting | `TemporalKnowledgeGraph` / `NodeMemoryStore` |
| Outcome-learned retrieval seed | `wip/experience-weighted-retrieval` branch |
| Engineering scaffolding | `core` protocols, strict typing, uv monorepo, package boundaries |

**Reuse the *lesson*, not the code, for:** the JEPA orchestrator (`packages/orchestrator/*`),
the REINFORCE / `outcome_scorer.py` self-referential reward machinery, and the slot decoder /
talker / instruction-refiner (served the orchestration path, irrelevant to an actuator model).

---

## 12. Open questions / parking lot

- ~~Physical two-store vs. one namespaced store~~ → **resolved**: one graph + **separate vector indexes
  per hemisphere** + native links (what broke = index pollution, not storage). See [`deep-dives/foundation.md`](deep-dives/foundation.md).
- **Multi-user** → **resolved (scope-now, defer-features)**: tenant/repo-scope the schema & logs from
  day 1 (cheap; irreversible-if-deferred), operate single-tenant, defer cross-tenant learning/privacy.
  See [`deep-dives/foundation.md`](deep-dives/foundation.md) and §14.
- What exactly constitutes an "episode" and its "why" (schema for Brain 1 nodes/edges)? The
  trajectory log (§13.11b) forces this; start with coarse deterministic boundaries
  (request-to-request or commit-to-commit), refine with data. → **being built as the experiential
  ingestion spine** ([`deep-dives/path-to-real-data.md`](deep-dives/path-to-real-data.md)).
- What is the outcome signal, concretely, per interaction (cited? edit passed tests? user kept it?)? → **§13.8.**
- Consolidation cadence (nightly batch vs. continuous) and how to evaluate it (held-out edge
  prediction for connection discovery; A/B on retrieval quality with vs. without consolidation).
- Contradiction/belief-revision representation (versioned beliefs + supersession edges?). → **§13.18**:
  versioned beliefs, D1–D3 detection (structural audit the standout), credibility-weighted coexistence
  revisited by a routine consolidation pass.
- Eval harness + pre-committed metrics *before* building learning layers (the Polynoica
  exit-criteria discipline). → **§13.12.**

---

## 13. Deep dive: outcome-learned retrieval → moved

The full deep dive (§13.1–13.20) now lives in
[`deep-dives/outcome-learned-retrieval.md`](deep-dives/outcome-learned-retrieval.md): the recall
ceiling, outcome-bent retrieval geometry, the bandit/representation loop, the exploration hierarchy,
hindsight relabeling, trajectory capture, episode segmentation, the "why", belief revision,
cross-hemisphere linking, and the eval harness. **All `§13.x` cross-references in this document point
into that file.**

---

## 14. Recurring design principles

The principles that *generate* the deep-dive designs — one small set produced all three frontier bets,
which is the strongest coherence signal we have. Check any new piece against these.

1. **Capture raw, derive views.** Only *raw capture* is irreversible-if-lost; groupings and
   interpretations (episode segmentation, the "why", "current truth") are **recomputable views** over
   the raw log. Get capture right at step 0/1; defer and re-derive the rest (incl. in dreaming).
2. **Deterministic where computable; learned only where not.** AST / git / type-checkers / `pytest` /
   symbol-overlap over latent approximations. Don't store what you can recompute (structural facts,
   gotchas, drift) — a learned model only ever distills a tool that already gives the exact answer.
3. **External-outcome arbitration + the model-interpretation firewall.** Every learning/credibility
   signal comes from *outside* the optimized space (tests, kept-vs-reverted, human, deterministic
   overlap). Model-generated content may be retrieved/proposed but **never validates itself** — the
   Polynoica self-reference trap in slow motion.
4. **Conservative against silent poisons.** Where a wrong automatic action destroys truth *invisibly*
   (belief supersession, episode mis-segmentation, false staleness), prefer **flag / coexist / propose**
   over auto-apply / delete; let time + outcomes arbitrate (credibility weights, routine reconciliation).
5. **Boring base + removable, measured novelty.** Build the proven baseline as the measuring stick;
   put novelty in a layer that **switches off** and must **beat the stick on pre-committed metrics** or
   it's cut. The axis is *measured vs. unmeasured*, not *easy vs. hard*.

**Recurring constraint — the gateway is the only surface.** The actuator is external; lean on
out-of-band, actuator-agnostic observation (file watcher / git hooks / test hook). Treat actuator
self-report as graceful enrichment that degrades to nothing — or forfeit "works with any editor."

**Recurring strategic thread — multi-user.** The cheap/deterministic layers work single-user; the
*differentiating learned* layers may be un-validatable without multi-user volume. It keeps surfacing as
the unlock — treat it as a first-class open decision, not a footnote.

---

## 15. Deep dives (index)

Detailed explorations live in [`deep-dives/`](deep-dives/). Each is a removable, gated layer on the
boring base — a *map of options, not a build mandate*.

- [Outcome-learned retrieval](deep-dives/outcome-learned-retrieval.md) — §13. Recall ceiling,
  outcome-bent geometry, exploration + hindsight relabeling, trajectory capture, segmentation, the
  "why", belief revision, cross-hemisphere linking, eval harness. **Done.**
- [Dreaming / consolidation](deep-dives/dreaming.md) — the offline integration pass (scheduler of gated
  passes; the DAG; safe-to-rerun). **Done (first pass).**
- [Foundation](deep-dives/foundation.md) — the boring base: the two resolved decisions (one-graph +
  separate indexes; multi-user scope-now), the gateway contract, the concrete **step-0/1 build spec**. **Done.**
- [Structural hemisphere (Brain 2)](deep-dives/structural-hemisphere.md) — re-derivable corpus graph;
  corpus-agnostic `Ingestor` seam + Python AST ingestor + k-hop graph built; calls (jedi→SCIP),
  multi-language, and document ingestors deferred behind the seam. **v0 built.**
- [Path to real data](deep-dives/path-to-real-data.md) — the honest framework-vs-real-data position, the
  two senses of "Brain 1 data", the **data-readiness ladder** (R0 instruments → R4 learning thesis), the
  **experiential ingestion spine** (closes §10 step 1's "capture the why"), and **dogfooding Thalamus on
  itself** as the bootstrap from framework to real data. **Spine built; dogfood (real usage) is next.**
- *Associative retrieval (HippoRAG): largely covered by §13.19 + Brain 2 k-hop; only PPR-vs-BFS ranking
  remains, a step-2 tuning decision.*
