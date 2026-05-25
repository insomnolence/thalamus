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

Pre-implementation. Design exploration captured in
[`docs/design-notes.md`](docs/design-notes.md). Spun out of the Polynoica project
on 2026-05-24 to be worked on independently over time.

## Core shape (current thinking)

- **Two hemispheres**, kept separate (a single polluted store caused real problems before):
  - **Brain 1 — Experiential / autobiographical:** what we did and *why*, preferences,
    history. Irreplaceable, append-mostly, consolidated over time.
  - **Brain 2 — Structural / codebase:** the AST dependency graph of the repo.
    Deterministic, re-derivable by re-parsing.
- **A single gateway conduit** (likely MCP) is the only thing the LLM agent talks to.
- **The LLM is an actuator** working in a tight, constraint-rich window fed by the brain.

See the design notes for the architecture, the research map, and the open questions.
