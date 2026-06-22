#!/usr/bin/env bash
# Launch Codex with the Thalamus brain attached as an MCP server — scoped to THIS project only.
#
# Codex has no per-project config (it reads only ~/.codex/config.toml), so rather than registering
# the brain machine-wide we inject it at launch with a `-c` override. The server definition lives
# here, in the repo, and is present only in sessions started through this script.
#
# Assumes the brain is already serving over HTTP — start it first with:
#   scripts/serve-http.sh
#
#   scripts/codex.sh                 # interactive Codex with the thalamus-http MCP server
#   scripts/codex.sh exec "..."      # any args pass straight through to `codex`
#
# Host/port/token mirror serve-http.sh; override via the same environment variables.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${THALAMUS_HTTP_HOST:-127.0.0.1}"
PORT="${THALAMUS_HTTP_PORT:-8787}"
URL="http://$HOST:$PORT/mcp"

# Build the inline TOML table for the override. Add bearer auth only when a token is configured
# (serve-http.sh reads the same THALAMUS_HTTP_TOKEN); Codex reads the env var by name at runtime.
if [[ -n "${THALAMUS_HTTP_TOKEN:-}" ]]; then
  SERVER="mcp_servers.thalamus-http={url=\"$URL\", bearer_token_env_var=\"THALAMUS_HTTP_TOKEN\"}"
else
  SERVER="mcp_servers.thalamus-http={url=\"$URL\"}"
fi

exec codex -c "$SERVER" "$@"
