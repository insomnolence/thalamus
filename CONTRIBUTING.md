# Contributing to Thalamus

Thanks for your interest. Thalamus is a research/dogfood system with a strong measurement
discipline, so contributions are very welcome — including, especially, **honest critique**. The
whole point of the built-in instruments (`verdict`, `rung-eval`, the proxy↔truth monitor) is that
claims can be checked. If a number looks wrong or a design seems to fool itself, that's a bug worth
filing.

By contributing, you agree your contributions are licensed under the project's
[Apache License 2.0](LICENSE).

## The discipline (please read before a substantial PR)

These are the project's load-bearing principles. A change that violates them is unlikely to merge
even if it "works":

1. **Boring base, removable/measured novelty.** Build the foundation from proven parts; put any
   novel layer *above* it, behind a clear interface, so it can be turned **off** and measured
   against the boring baseline. New capability should be ablatable.
2. **Learning signals are external behavioral acts — never self-reference.** Any signal that ranks,
   credits, or demotes a memory must come from an external act (it was *used* / *superseded* /
   *co-changed* / recent / graph-central), **never** from the model grading its own prose. A
   self-referential reward is the failure mode this project exists to avoid.
3. **Define the metric before the learning layer.** If you add something that learns, say how you'll
   measure whether it helped — ideally as an ablation the eval harness can run.
4. **Prefer deterministic analysis** (parsers, type-checkers, graph traversal) over a learned model
   wherever the answer is computable. Don't approximate with latent geometry what a tool gives exactly.
5. **Modular & swappable.** Every component (hemispheres, retrieval rungs, dreaming passes) sits
   behind a protocol so it can be pulled, replaced, or omitted.

## Development setup

Requirements: [`uv`](https://docs.astral.sh/uv/), Docker (for Neo4j), Python 3.12+.

```bash
docker compose up -d                     # local Neo4j
uv sync --all-packages --all-extras      # install the workspace (NB: not a bare `uv sync` — it
                                         # strips the optional extras the encoder/SCIP tests need)
```

## The gate (must pass before you open a PR)

CI and reviewers expect this to be green. Run it with the Neo4j env vars **unset** so the
integration tests use their own disposable instance, never your local brain:

```bash
unset THALAMUS_NEO4J_URI THALAMUS_TEST_NEO4J_URI
uv run ruff check packages/
uv run mypy --strict packages/*/src
uv run pytest -q
```

- `ruff` clean, `mypy --strict` clean, all tests passing.
- Line length is 100.
- Neo4j integration tests are **skipped** unless you set `THALAMUS_TEST_NEO4J_URI` to a *disposable*
  instance (never the one you serve a real brain from — a routine test run once wiped accumulated
  data, hence the guard).

## Pull requests

- Keep them **small and focused** — one concern per PR.
- Commit messages: state **what changed and why**. No phase labels.
- New behavior comes with tests; new learning/ranking behavior comes with (or references) the eval
  that measures it.
- If your change touches a measurement instrument, include the before/after numbers from a real run.

## Reporting issues

Bugs, design critiques, and "this metric is lying to you" reports are all welcome. For anything
measurement-related, include the command you ran and its output so it's reproducible.
