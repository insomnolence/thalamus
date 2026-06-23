# M-1a — gotcha-conversion probe (how to run it)

M-1a asks one narrow, falsifiable question: **conditional on the brain holding the decisive memory
for a task, does surfacing it cause a fresh actuator to take an objectively better action — above
mere salience?** It is a *necessary-condition* probe of the retrieval→actuator delivery link, **not**
the full "does the brain help" thesis. Read the frozen protocol first:
[`m1a_preregistration.md`](m1a_preregistration.md).

It is **contained and opt-in**: a script (`thalamus m1a-eval`) under `packages/eval/`, **not** part of
the serving brain. It calls an external LLM *actuator* (swappable) and judges its output with a
**deterministic oracle** — never a model grading prose (the §14 firewall).

## Quickstart (local, Ollama)

```bash
# any chat-capable model works; a coding model is the natural actuator
uv run python -m thalamus.cli m1a-eval \
  --cases docs/eval/m1a_cases.example.json \
  --actuator ollama --model qwen2.5-coder:7b \
  --trials 4 --audit /tmp/m1a-audit.jsonl
```

The example cases are an **illustrative template**, not a frozen scientific set — use them to learn the
format and smoke-test the pipeline, then author your own (see *Writing cases*).

## Actuators

Pick a backend with `--actuator`; all use the same prompt envelope, so only the model differs.

| `--actuator` | Model examples | Config |
|---|---|---|
| `ollama` (default) | `qwen2.5-coder:7b`, `llama3.1:8b` | local server; `--base-url` (default `http://localhost:11434`) |
| `claude` / `anthropic` | `claude-...` | `ANTHROPIC_API_KEY` |
| `openai` / `codex` | `gpt-...` | `OPENAI_API_KEY`; `OPENAI_BASE_URL` for any OpenAI-compatible endpoint |
| `gemini` | `gemini-...` | `GEMINI_API_KEY` |

Backends are dependency-free (stdlib HTTP), so no extra install is needed. Cost note: a full run is
`4 arms × --trials × cases` actuator calls — keep `--trials` low while iterating.

## Drafting cases from a brain (`m1a-draft`)

The brain→cases bridge. Point it at a running brain and a candidate *decisive* memory; it recalls
(read-only) and renders the four arm blocks from the **actually-surfaced** memories — `full` is the
real recalled brief, `content_ablation` is that brief minus the decisive memory.

```bash
uv run python -m thalamus.cli m1a-draft \
  --neo4j-uri bolt://localhost:7688 --neo4j-password <pw> \
  --repo-id dollhouse --tenant local --k 8 \
  --memory-id retained:<decisive> \
  --task "<task cue from an INDEPENDENT source>" > draft.json
```

It emits a **draft** case with a `_review` checklist and a TODO oracle. It is *not* runnable as-is —
you must (1) author the deterministic oracle, (2) confirm the bars below, (3) drop the `_review` field.
If the decisive memory isn't in the top-`k` recall, the draft warns: the brain wouldn't surface it, so
it isn't a valid positive case (raise `--k` or pick a different memory/task). Collect several vetted
drafts into one JSON list — that's your frozen cases file.

## Writing cases

Cases live in a JSON list (frozen before a real run). Each case:

```jsonc
{
  "id": "encoder-index-rebuild",
  "set": "positive",                       // positive | negative_control | adversarial
  "memory_id": "retained:...",             // provenance (optional)
  "task": "<task cue from an INDEPENDENT source — the original commit/issue text>",
  "arms": {
    "off":              "<length-matched placebo context, no memory>",
    "salience":         "<off + a generic content-free nudge, e.g. 'watch for known gotchas'>",
    "content_ablation": "<the full brief MINUS the one relevant memory>",
    "full":             "<the full brief WITH the relevant memory>"
  },
  "oracle": {                              // deterministic verdict on the actuator OUTPUT
    "required":  ["re-?build.*index"],     // all must match (case-insensitive regex)
    "forbidden": ["\\beval\\("]            // none may match
  }
}
```

The pre-registration's hard rules (don't skip these — they're what make a result mean anything):

- **The memory must be an episode / why / decision, never a warning-shaped answer key.** If the `full`
  block literally states the avoidance, that's answer leakage — exclude it.
- **The task cue comes from an independent source** (the original commit/issue), *not* co-authored with
  the gotcha.
- **Length/format-match the four blocks** so "more tokens" is never the cue.
- **A `negative_control` set is mandatory** (brain holds no relevant memory → expect `full ≈ off`). The
  positive effect must *clear this null*, not merely beat zero.
- **The oracle is code** (regex/AST/lint/test). If a case can't get a deterministic oracle, **exclude
  it** — do not substitute an LLM judge. (An LLM is allowed only as a blind, arm-stripped pre-screen.)

## Reading the report

```
M-1a result
  full - content_ablation     n=12  mean δ=+0.31  90% CI [+0.12, +0.49]  sign p=0.018
  full - salience             n=12  mean δ=+0.22  90% CI [+0.05, +0.38]  sign p=0.039
  full - off                  n=12  mean δ=+0.40  ...
  negative-control δ (should be ~0): +0.02
  success criteria (all must hold for a positive M-1a):
    [PASS] primary_delta_ci_excludes_0
    [PASS] clears_negative_control
    [PASS] beats_salience
    [PASS] has_negative_control
  VERDICT: POSITIVE
```

- **Primary contrast** is `full − content_ablation` (isolates *the memory*, not the brain-as-bundle).
- A **null is a valid result.** If the effect vanishes once the negative-control and salience nulls are
  subtracted, the instrument caught a tautology — that's it working, not failing.

## Scope of what's implemented

The harness, the four arms, the deterministic oracles, the per-case sign test, and a seeded cluster
bootstrap CI are implemented and tested. The pre-registration also calls for Wilcoxon signed-rank, a
hierarchical Beta-Binomial posterior, and anytime-valid (e-process) CIs — those are **additive
refinements for the final frozen run and are not yet implemented**; add them before quoting a headline.

## Workflow

1. Build a handful of cases (3–5) with deterministic oracles; smoke-run with `--trials 2` on Ollama.
2. If the pipeline produces clean per-case avoid-rates, scale to the full set (≥10 positive + ≥6
   negative-control), **freeze and commit the cases file**, then run with `--trials 8`.
3. Report `mean δ + CI + per-case k/n` — never a bare win count. A green M-1a is **never** the thesis
   verdict (see the pre-registration's "what this does NOT license").
