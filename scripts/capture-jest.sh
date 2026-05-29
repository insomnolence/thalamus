#!/usr/bin/env bash
# Capture a jest run as a Tier-2 outcome — the JavaScript counterpart to the in-process pytest
# plugin. jest is JS, so Thalamus can't watch it in-process; instead jest emits a standard JUnit
# report (via jest-junit, passed as CLI flags so NOTHING is committed to the target project) and
# `thalamus capture-tests` ingests it. A full run is the terminal Tier-2 validation; --aggregate
# collapses jest's one-suite-per-file output so ANY red file makes the whole run FAILED (the
# negative outcome signal — not just green passes).
#
# Prereq:  npm install -g jest-junit   (or add it to the project's devDependencies)
#
# Env:
#   THALAMUS_SESSION_ID  (required) the active serve session id to join this run to
#                        (published by `serve` at <repo>/.thalamus/session/current.json)
#   THALAMUS_REPO        brain repo whose .thalamus/logs receives the event (default: <ts-dir>)
#   THALAMUS_REPO_ID / THALAMUS_TENANT   scope overrides (default: repo dir name / local)
#   THALAMUS_JUNIT_OUT   where jest writes the report (default: <ts-dir>/.thalamus-junit.xml)
#
# Usage:
#   THALAMUS_SESSION_ID=$(jq -r .session_id <repo>/.thalamus/session/current.json) \
#     scripts/capture-jest.sh /path/to/ts-project [extra jest args...]
set -euo pipefail

TS_DIR="${1:?usage: capture-jest.sh <ts-project-dir> [jest args...]}"
shift || true
SESSION="${THALAMUS_SESSION_ID:?set THALAMUS_SESSION_ID to the active serve session id}"
REPO="${THALAMUS_REPO:-$TS_DIR}"
OUT="${THALAMUS_JUNIT_OUT:-$TS_DIR/.thalamus-junit.xml}"

# Run jest with the jest-junit reporter via CLI flags only (no committed jest config change).
# `|| true`: jest exits non-zero on failures, but a failing run is exactly the signal to capture.
( cd "$TS_DIR" && JEST_JUNIT_OUTPUT="$OUT" npx jest --reporters=default --reporters=jest-junit "$@" ) || true

if [[ ! -f "$OUT" ]]; then
  echo "capture-jest: no JUnit report at $OUT (is jest-junit installed?)" >&2
  exit 1
fi

exec python -m thalamus.cli capture-tests \
  --repo "$REPO" --junit "$OUT" --session-id "$SESSION" --terminal --aggregate \
  ${THALAMUS_REPO_ID:+--repo-id "$THALAMUS_REPO_ID"} \
  ${THALAMUS_TENANT:+--tenant "$THALAMUS_TENANT"}
