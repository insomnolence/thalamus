# Connecting a coding agent to Thalamus

Thalamus is exposed to your agent over **MCP** (Model Context Protocol). It speaks two transports:

- **stdio** — the agent spawns its own `thalamus … serve` process per session. Simplest; good for
  one agent at a time (Claude Code, Gemini CLI).
- **HTTP** — you run one long-lived server and any number of agents connect to it. Better for
  many clients or an agent (like Codex) that has no per-project config. See
  [`../scripts/serve-http.sh`](../scripts/serve-http.sh).

Before any of this, do the [Quickstart](../README.md#quickstart): start Neo4j, `uv sync
--all-packages --all-extras`, and (optionally) `remember` / `sync` some context.

The example files here are **templates** — copy them into place and edit the marked values. None
contains a real secret; the only credential is the local-dev Neo4j password, which matches the one
in [`../docker-compose.yml`](../docker-compose.yml).

## What you must fill in

| Field | What it is | Change it when… |
|-------|-----------|-----------------|
| `--repo-id` (`my-project`) | A stable name for this codebase's brain | Always — name it for your repo |
| `THALAMUS_NEO4J_PASSWORD` (`thalamuspw`) | Neo4j password | You changed it in `docker-compose.yml` / use a remote Neo4j |
| `THALAMUS_NEO4J_URI` (`bolt://localhost:7687`) | Where Neo4j lives | Neo4j is not on localhost |
| `--repo` (`.`) | The repo the brain is scoped to | The agent's working dir isn't the repo root |

---

## Claude Code (stdio)

1. Copy [`claude-code.mcp.json`](claude-code.mcp.json) to a file named `.mcp.json` at the root of
   your project (drop the `_comment` line — strict JSON has no comments).
2. Edit `--repo-id` (and the password if it isn't the local default). Claude Code expands
   `${VAR:-default}`, so the exports from the Quickstart are picked up automatically.
3. Start Claude Code in the repo and approve the `thalamus` server. Verify with
   `claude mcp get thalamus`.

It exposes `recall`, `remember`, `record_usage`, and `plan`.

## Gemini CLI (stdio)

1. Merge the `mcpServers` block from [`gemini-settings.json`](gemini-settings.json) into your
   `.gemini/settings.json` (project-local) or `~/.gemini/settings.json` (global). Drop the
   `_comment` line.
2. Edit `--repo-id` and the password as needed.
3. Launch `gemini` from the repo root.

## Codex (HTTP)

Codex reads only `~/.codex/config.toml` (no per-project config), so attach the brain at launch over
HTTP instead:

1. Start the server: [`../scripts/serve-http.sh`](../scripts/serve-http.sh) (defaults to
   `http://127.0.0.1:8787/mcp`).
2. Launch Codex with the brain injected: [`../scripts/codex.sh`](../scripts/codex.sh). It passes a
   `-c mcp_servers.thalamus-http=…` override through to `codex`; any extra args pass straight
   through (`scripts/codex.sh exec "…"`).
3. For a non-local bind, set `THALAMUS_HTTP_HOST` and a `THALAMUS_HTTP_TOKEN` (bearer auth) — both
   scripts read the same variables. The server warns if you bind off-localhost without a token.
