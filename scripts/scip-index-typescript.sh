#!/usr/bin/env bash
# Build a SCIP index for a TypeScript/JavaScript project — the out-of-band "parse" step
# that the Thalamus SCIP ingestor consumes. Brain 2's code corpus for a TS project is
# derived from this `.scip` artifact, not by parsing TS in-process (precise TS resolution
# needs the TS type-checker, a Node tool — so we delegate to Sourcegraph's scip-typescript).
#
# Prereqs (one-time):  npm install -g @sourcegraph/scip-typescript
# The target project needs its deps installed (npm ci / npm install) and a tsconfig.json.
#
#   scripts/scip-index-typescript.sh <project-dir> [output.scip]
#   scripts/scip-index-typescript.sh /path/to/sample-project/mcp-server
#
# Then serve that corpus:
#   python -m thalamus.cli serve \
#     --repo <project-dir> --code-language typescript --scip-index <project-dir>/index.scip
#
# IMPORTANT: the `.scip` index is the structure source-of-truth; the brain detects file
# changes from disk but re-derives structure from this artifact. Re-run this script after
# the TypeScript changes, or a serve will drop/re-add nodes from a stale parse.
set -euo pipefail

PROJECT="${1:?usage: scip-index-typescript.sh <project-dir> [output.scip]}"
OUTPUT="${2:-$PROJECT/index.scip}"

if ! command -v scip-typescript >/dev/null 2>&1; then
  echo "scip-typescript not found. Install it:  npm install -g @sourcegraph/scip-typescript" >&2
  exit 1
fi
if [[ ! -f "$PROJECT/tsconfig.json" ]]; then
  echo "no tsconfig.json under $PROJECT — scip-typescript needs one to index the project." >&2
  exit 1
fi

echo "thalamus: indexing TypeScript at $PROJECT -> $OUTPUT" >&2
( cd "$PROJECT" && scip-typescript index --output "$OUTPUT" )
echo "thalamus: wrote $OUTPUT" >&2
