# Thalamus

[![gate](https://github.com/insomnolence/thalamus/actions/workflows/gate.yml/badge.svg)](https://github.com/insomnolence/thalamus/actions/workflows/gate.yml)
[![license: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![python: 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

**A self-hosted, code-aware brain for coding agents.** Long-term memory that survives across
sessions, learns your codebase *and* the decisions behind it, links *why you did something* to
*where it lives in the code*, and ships with the instruments to prove it's actually helping.

Runs locally on your machine (Neo4j + a local embedding model), exposed to your agent through a
single MCP endpoint. No cloud, no account, no per-seat pricing.

> **Status — honest version.** This is a working, daily-dogfooded **research system**, not a polished
> product. Both hemispheres are live, the learning loop runs, and the measurement is real and
> reproducible — but it's validated at single-user scale, the setup has rough edges, and the headline
> "more use → more useful" thesis isn't *proven* yet (the instrument to prove it is built; see
> [Does it actually work?](#does-it-actually-work)). If that framing appeals to you, read on.

## Why

LLM agents are stateless. Every task starts from scratch — they re-derive the same context, repeat
the same mistakes, and forget *why* the code is the way it is. The common fix (frameworks and files
used as "memory") forgets, never updates stored knowledge, and is usually ignored.

Thalamus models the thing that actually works: a brain with **experiences** (what you did and *why*)
and a **structural map** of the code, that an agent leans on — and that gets more useful the more
it's used.

## What it's built around

Four ideas hold the project together:

- **Code-aware.** A whole hemisphere is the **structural graph of your code** — AST / SCIP dependency
  graph, call edges, a `plan` blast-radius tool ("change this → here's what breaks") — cross-linked to
  the experiential memory of *why* it's that way. It's a brain for *coding*, not chat.
- **Deterministic structure, not LLM-extraction.** The code graph is derived from parsers and
  indexers — exact, not a language model's noisy guess at entities and relationships.
- **Honest measurement, built in.** A proxy↔truth `verdict`, a rung-ablation eval, and calibrated
  exploration — the apparatus to tell whether the brain is *really* helping, instead of a self-reported
  "it improves things by N%" claim. Every learning signal is an external behavioral act (used /
  superseded / co-changed / graph-central), never the model grading its own output.
- **Local, self-hosted, open.** Your code and decisions never leave your machine.

## How it works

- **Two separated hemispheres** (a single polluted store caused real problems in an earlier project of
  ours):
  - **Brain 1 — Experiential:** what you did and *why* — decisions, gotchas, preferences, and episodes
    mined from your commits. Append-mostly, consolidated over time.
  - **Brain 2 — Structural:** a re-derivable corpus graph — code (AST / SCIP + call graph), docs,
    text, external findings — declared per project via `[[corpus]]` entries in `thalamus.toml`.
    Language-agnostic; auto-refreshed from current source.
- **A single gateway (MCP)** is the only thing your agent touches. Recall *fuses* both hemispheres:
  the relevant memory **plus** the structural context it's about.
- **Cross-hemisphere links** connect "why we did X" → "where X lives in the code."
- **It consolidates and learns from itself.** Like sleep, an offline *dreaming* phase runs on a
  background clock: re-derives Brain 2 from current code, re-links memories to the code they touched,
  demotes superseded beliefs, and folds the brain's own usage history into durable state — so recall
  improves over time without a restart. Adaptations are removable and measured against a boring
  baseline; the raw logs are a disposable write-ahead buffer, the brain is the system of record.

Architecture, research map, and roadmap: [`docs/design-notes.md`](docs/design-notes.md),
[`docs/deep-dives/`](docs/deep-dives/), [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Quickstart

**Requirements:** [`uv`](https://docs.astral.sh/uv/), Docker (for Neo4j), Python 3.12+. The encoder
runs locally via [`fastembed`](https://github.com/qdrant/fastembed) (ONNX Runtime — no PyTorch); first
run downloads a small embedding model ([BGE-small](https://huggingface.co/BAAI/bge-small-en-v1.5),
MIT-licensed) from the Hugging Face Hub. An offline, dependency-free fallback encoder ships for tests
and air-gapped use.

**1. Start Neo4j and install the workspace** (a [`docker-compose.yml`](docker-compose.yml) is included):

```bash
docker compose up -d
uv sync --all-packages --all-extras            # installs the CLI + the fastembed (ONNX) encoder
export THALAMUS_NEO4J_URI=bolt://localhost:7687
export THALAMUS_NEO4J_USER=neo4j
export THALAMUS_NEO4J_PASSWORD=thalamuspw      # local dev password; change it for anything real
```

**2. Teach it something durable** about *this* repo, with a file footprint for structural context:

```bash
uv run python -m thalamus.cli remember \
  --repo . --kind constraint \
  --text "Changing the vector encoder requires rebuilding compatible indexes." \
  --why "Embeddings from a different encoder aren't comparable; recall silently degrades." \
  --file packages/store/src/thalamus/store/neo4j_store.py
```

**3. Materialize episodes from your git history**, then serve the brain over MCP:

```bash
uv run python -m thalamus.cli sync  --repo .
uv run python -m thalamus.cli serve --repo .
```

**4. Point your agent at it.** Copy a config template from [`examples/`](examples/) — Claude Code,
Gemini CLI, and Codex are covered, with [`examples/README.md`](examples/README.md) explaining what to
fill in. For Claude Code: copy [`examples/claude-code.mcp.json`](examples/claude-code.mcp.json) to a
`.mcp.json` at your repo root, set `--repo-id`, then approve the `thalamus` server (`claude mcp get
thalamus` to verify). The brain exposes `recall`, `remember`, `record_usage`, and `plan` — ask the
agent to recall prior decisions, or to `plan` the blast radius of a change, and it answers from the
brain.

`remember` stores high-value facts (`decision` / `constraint` / `gotcha` / `investigation` /
`preference`); `sync` derives commit episodes; `serve` exposes bounded MCP recall and logs retrieval +
usage beneath `.thalamus/logs/`. For non-Python codebases, declare `[[corpus]]` tables in
`thalamus.toml` (any language with a SCIP indexer works with no new code).

## Does it actually work?

The point of the measurement apparatus is to answer this honestly rather than assert it. On real
dogfooding (this repo + a separate code-rich project), via the built-in `health` / `verdict` /
`rung-eval` tools:

- **Surfaced context maps to real committed work** ~65–70% of the time on the code-rich brain
  (`utility@5`), and the proxy↔truth monitor shows the proxy **tracks** truth (positive alignment, no
  reward-hacking flagged).
- **A reliably-useful core forms with use** — dozens of memories recalled-and-used across multiple
  distinct sessions.
- **The structural-centrality retrieval rung is a clean win** (it ranks the memory the agent actually
  used higher, on both recall and MRR) in a de-leaked ablation — that's *why* it's enabled by default.

**Honest caveats** (the same instruments enforce them): this is single-user scale; the over-time
"more use → more useful" *slope* is confounded (task drift, attribution lag) and is **not** cleanly
measurable yet — proving it needs a controlled brain-on/off ablation, which is designed but not run
(see [`docs/deep-dives/path-to-real-data.md`](docs/deep-dives/path-to-real-data.md) and the M-1
pre-registration). Reproduce any of the above on your own repo:

```bash
uv run python -m thalamus.cli health  --repo . --code-root .
uv run python -m thalamus.cli verdict --repo .
```

## Status & scope

- **Is:** a memory/retrieval system that feeds an LLM **actuator** through one gateway. Built for
  agentic *coding* on codebases you control.
- **Is not:** a product, a hosted service, or an attempt to replace the LLM. The LLM stays the
  actuator; Thalamus gives it the whole-system picture so its local edits are globally informed.
- **Audience:** developers running coding agents who want a local, inspectable, measurable brain.
- **Maturity:** research / dogfood. Expect setup friction and sharp edges; the design discipline is
  "proven boring base + removable, measured novelty," and the docs are honest about what's unproven.

## License & contributing

Thalamus is licensed under [Apache-2.0](LICENSE). Contributions, issues, and honest critique welcome —
the measurement tools exist precisely so claims can be checked; if a number looks wrong, that's a bug
worth filing.

**Acknowledgments.** Semantic recall uses the [BGE-small](https://huggingface.co/BAAI/bge-small-en-v1.5)
embedding model (`BAAI/bge-small-en-v1.5`, MIT-licensed), downloaded from the Hugging Face Hub at
runtime — it is **not** redistributed here. SCIP code-graph support derives from
[`sourcegraph/scip`](https://github.com/sourcegraph/scip) (Apache-2.0; see [`NOTICE`](NOTICE)). A
deterministic, offline encoder ships as a dependency-free fallback.
