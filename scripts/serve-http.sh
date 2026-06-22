#!/usr/bin/env bash
# Launch the Thalamus brain over Streamable HTTP — the long-running, many-client transport.
#
# Defaults to localhost:8787 with no auth (local testing). For a LAN box, set THALAMUS_HTTP_HOST
# to a reachable address and THALAMUS_HTTP_TOKEN to a secret (bearer auth). Any extra args pass
# straight through to `serve` (e.g. --no-resolve-calls for a faster start, --no-dream-tick).
#
#   scripts/serve-http.sh                              # http://127.0.0.1:8787/mcp
#   scripts/serve-http.sh --no-resolve-calls           # faster startup while testing
#   THALAMUS_HTTP_HOST=0.0.0.0 THALAMUS_HTTP_TOKEN=secret scripts/serve-http.sh
#
# Stop with Ctrl-C. Knowledge is durable in Neo4j, so a restart (e.g. after a code update)
# rebuilds only the in-memory views (fast/incremental); connected agents reconnect automatically.
set -euo pipefail
cd "$(dirname "$0")/.."

# Brain backends — same defaults as .mcp.json; override via the environment.
export THALAMUS_NEO4J_URI="${THALAMUS_NEO4J_URI:-bolt://localhost:7687}"
export THALAMUS_NEO4J_USER="${THALAMUS_NEO4J_USER:-neo4j}"
export THALAMUS_NEO4J_PASSWORD="${THALAMUS_NEO4J_PASSWORD:-thalamuspw}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export THALAMUS_REPO_ID="${THALAMUS_REPO_ID:-thalamus}"
export THALAMUS_TENANT="${THALAMUS_TENANT:-local}"
# THALAMUS_HTTP_TOKEN (if set) and THALAMUS_HTTP_ALLOWED_ORIGINS are read by `serve` from the env.

HOST="${THALAMUS_HTTP_HOST:-127.0.0.1}"
PORT="${THALAMUS_HTTP_PORT:-8787}"

if [[ "$HOST" != "127.0.0.1" && "$HOST" != "localhost" && -z "${THALAMUS_HTTP_TOKEN:-}" ]]; then
  echo "thalamus: WARNING — binding $HOST (off-localhost) with no THALAMUS_HTTP_TOKEN; anyone on" \
       "the network could read/write this brain. Set THALAMUS_HTTP_TOKEN or front it with a VPN." >&2
fi

echo "thalamus: HTTP serve on http://$HOST:$PORT/mcp  (Ctrl-C to stop)" >&2
exec uv run --package thalamus-routing --extra fastembed python -m thalamus.cli serve \
  --transport http --host "$HOST" --port "$PORT" "$@"
