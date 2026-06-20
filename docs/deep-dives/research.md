# The `research` tool (C-5 / §16 step 6) — design groundwork

*Status: **design captured, not built** (P3). This is recovered groundwork — a design discussion held
2026-06-16 that was never committed or written to the brain and nearly lost. Read alongside ROADMAP C-5
and [`planning.md`](planning.md) (the `plan` tool it complements).*

## What it is

A deeper cross-hemisphere **"what do we know about X"** synthesis tool. Where `plan` starts from a
**code target** and shows the system around it, `research` starts from a **question/topic** (no code
anchor) and gathers everything relevant into one brief — the case `plan` can't help with: *"how does
auth work across the system?"*, *"what have we decided about rate limiting?"*

## plan vs. research — the distinction (the load-bearing question)

The failure mode to avoid is research being "recall with extra steps." The clean separation:

| | **plan** | **research** |
|---|---|---|
| **Entry point** | a code target (a symbol/file you're about to touch) | a question / topic (NL — no code anchor) |
| **Mechanism** | deterministic graph **blast radius** from the anchor + the why attached | broad **cross-hemisphere retrieval** around the topic, then **synthesis** |
| **Answers** | *"I'm changing X — what breaks and what do I know about it?"* | *"What does the brain know about Y / how does Y work / what have we decided?"* |
| **Dominant knowledge** | the structural graph (+ experiential cross-links) | the full reference layer — memories + docs + findings + code, fused |
| **Shape** | precise, bounded, anchored, deterministic | broad, exploratory, unanchored, synthesis-heavy |

Mental model: **plan starts from a tree and shows you the forest around it; research starts from a
question about the forest and gathers everything relevant.** plan is for *editing*; research is for
*understanding before you even know where to start*.

## Two honest tensions (why it isn't the obvious next build)

1. **Its core is the part we deliberately deferred for `plan`.** plan's value is its *deterministic*
   core; NL **synthesis** was punted because it is L3-gated and unmeasurable. research is *mostly*
   synthesis — gathering is easy (a richer recall), but turning scattered evidence into a coherent
   topic brief is the unmeasurable LLM step. research inherits plan's hardest deferred problem as its
   *core*, not an optional layer. (This is the §14 "define the eval first" line.)
2. **It has to earn its place over `recall`.** `recall` already does cross-hemisphere relevance
   retrieval. research is only worth a separate tool if it does what recall can't: **decompose** the
   question into sub-queries, gather across **all** of Brain 2 (esp. docs + findings, which plan barely
   touches), **dedup/organize** the evidence, and surface **cross-cutting connections** — not "recall
   + a summary."

Where it would genuinely shine: it's the tool that finally exercises **Brain-2's reference breadth**
(docs + analysis findings) that the vision wanted but neither `plan` (code-graph-centric) nor today's
tools lean on. That breadth is underpopulated today.

## Recommendation (the recovered verdict)

**Don't build it as "the next big thing" yet.** Its centre of gravity is synthesis (unmeasurable) and
reference-knowledge breadth (the brain is thin on it now). Preconditions before investing:
(a) more *content* in Brain 2 for it to synthesize, and (b) a decision on **how we'd know it's any
good** (an eval — cf. M-2's gotcha-case approach for plan).

If/when a real need appears, the first step is a **thin experiment**: *deep multi-pass recall +
organize, no fancy synthesis* — see whether gather-and-organize alone is useful before building the
synthesis layer.

## What today's work already builds toward it

The 2026-06-16 batch quietly laid real substrate: **C-2** (findings/docs `annotates` edges → fuse
non-code reference knowledge into code context), **C-3a** (relevance ranking of a gathered set), and
**L-R2** (structural-centrality weighting). A `research` tool is largely *plan's gather + ranking +
cross-hemisphere fusion, pointed at a topic instead of a target* — so the cost to build it has dropped.
