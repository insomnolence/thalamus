# Project Thalamus

A persistent **brain** for agentic LLMs — long-term memory, recall, and understanding
that survives across sessions, updates itself, and gets more useful the more it is used.

## Why

LLMs are predictive and stateless. They have no real memory or understanding. The current
state of practice — frameworks and files used as "memory" — forgets, never updates stored
knowledge, and is often ignored or useless. Thalamus models the thing that actually works
for this: a brain with experiences and consolidated knowledge that an agent can lean on.

## What it is *not*

- It does **not** try to replace the LLM (that was Polynoica's failed bet).
- It does **not** put a JEPA / world model in the execution path. JEPA was explicitly dropped
  for this project — see the design notes for the full reasoning.
- It does **not** make an exotic model (JEPA, LNN, SNN, Hopfield, …) the *foundation*.
  Novelty lives in a removable, measurable layer *on top of* a working base.

## Status

The step-0/1 baseline is implemented: scoped experiential storage, AST-derived
structural context, BGE retrieval, MCP recall, raw trajectory/retrieval/usage logs,
explicit retained memories, and an evaluation harness. Design exploration remains in
[`docs/design-notes.md`](docs/design-notes.md); current work is to dogfood the memory
surface on real coding sessions before adding learned ranking.

## Core shape (current thinking)

- **Two hemispheres**, kept separate (a single polluted store caused real problems before):
  - **Brain 1 — Experiential / autobiographical:** what we did and *why*, preferences,
    history. Irreplaceable, append-mostly, consolidated over time.
  - **Brain 2 — Structural / codebase:** the AST dependency graph of the repo.
    Deterministic, re-derivable by re-parsing.
- **A single gateway conduit** (likely MCP) is the only thing the LLM agent talks to.
- **The LLM is an actuator** working in a tight, constraint-rich window fed by the brain.

See the design notes for the architecture, the research map, and the open questions.

## Dogfood Workflow

Run a durable local Brain 1 with Neo4j:

```bash
docker volume create thalamus-neo4j-data
docker run -d --name thalamus-neo4j --restart unless-stopped \
  -v thalamus-neo4j-data:/data \
  -e NEO4J_AUTH=neo4j/thalamuspw -p 7687:7687 neo4j:5
export THALAMUS_NEO4J_URI=bolt://localhost:7687
export THALAMUS_NEO4J_USER=neo4j
export THALAMUS_NEO4J_PASSWORD=thalamuspw
```

Commands default to real BGE embeddings. When invoking from the workspace, enable the
routing package's optional encoder dependency:

```bash
uv run --package thalamus-routing --extra bge python -m thalamus.cli remember \
  --repo . --kind decision \
  --text "Memory and structural identities are scoped by tenant and repository." \
  --why "Unscoped ids can overwrite another repository's facts." \
  --file packages/core/src/thalamus/core/types.py

uv run --package thalamus-routing --extra bge python -m thalamus.cli sync --repo .

uv run --package thalamus-routing --extra bge python -m thalamus.cli serve --repo . \
  --max-structural-items 12 --max-memory-chars 1000
```

`remember` stores high-value explicit facts (`decision`, `constraint`, `gotcha`,
`investigation`, or `preference`) and can attach file footprints for structural context.
`sync` materializes derived commit episodes. `serve` exposes bounded MCP recall and logs
retrieval and usage events beneath `.thalamus/logs/`.

Claude Code can use the project-scoped [`.mcp.json`](.mcp.json) server configuration once
Neo4j is running. On first use, approve the project MCP server when Claude prompts; verify
it with `claude mcp get thalamus`. The MCP server exposes `recall`, `record_usage`, and
`remember`; ask Claude to retain durable decisions or gotchas during a task, for example:

```text
Use the Thalamus remember tool to store this constraint: changing the vector encoder
requires rebuilding compatible indexes. This applies to packages/store/src/thalamus/store/neo4j_store.py.
```

Memories written through MCP are semantically recallable immediately. Optional related-file
structural links are resolved the next time the server starts, along with the normal
Brain-2 derived-view rebuild.

To attach a terminal test result to a real task session:

```bash
uv run python -m thalamus.cli capture-tests --repo . --junit report.xml \
  --session-id <session-id> --terminal
```
