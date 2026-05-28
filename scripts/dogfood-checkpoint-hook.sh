#!/usr/bin/env bash
# Thalamus dogfood checkpoint — Claude Code Stop hook helper.
#
# Fires only when there's something for the agent to act on:
#   - recent recall events whose event_id has no matching record_usage entry
#
# Emits the reminder as BOTH a user-visible systemMessage and as
# hookSpecificOutput.additionalContext so it lands in the model's next context
# turn. Silent when nothing is unrecorded — no noise on every Stop.
#
# Wired from .claude/settings.local.json (project-local, gitignored).

set -u

REPO="${CLAUDE_PROJECT_DIR:-$(pwd)}"
LOGS="$REPO/.thalamus/logs"
RETRIEVAL="$LOGS/retrieval.jsonl"
USAGE="$LOGS/usage.jsonl"

# Nothing to compare against -> nothing to nudge about.
[ -f "$RETRIEVAL" ] || exit 0

# event_ids from the most recent 20 recalls (one session typically << 20)
recent=$(tail -20 "$RETRIEVAL" 2>/dev/null \
  | jq -r '.event_id // empty' 2>/dev/null \
  | sort -u)

recorded=""
if [ -f "$USAGE" ]; then
  recorded=$(jq -r '.event_id // empty' "$USAGE" 2>/dev/null | sort -u)
fi

# Recent recalls minus the ones already recorded as used.
unrecorded=$(comm -23 <(printf '%s\n' "$recent") <(printf '%s\n' "$recorded") 2>/dev/null \
  | grep -c . 2>/dev/null || echo 0)

# Only nudge when there are multiple unrecorded recalls; one-offs are noise.
#
# Schema note: Stop hooks only accept {continue, suppressOutput, stopReason, decision,
# reason, systemMessage, terminalSequence, permissionDecision}. `hookSpecificOutput`
# (with additionalContext) is valid for PreToolUse / UserPromptSubmit / PostToolUse /
# PostToolBatch, NOT for Stop. So we emit systemMessage only — shown in the UI,
# inviting the agent (and user) to close the open loops in the next turn.
if [ "${unrecorded:-0}" -gt 2 ]; then
  msg="Thalamus dogfood checkpoint — ${unrecorded} recent recall(s) have no record_usage. \
Close the T1 loop on any that shaped a response (the '# retrieval_event_id' line in each \
recall response is the key; this is what populates proxy↔truth). Also: any gotchas/decisions \
worth remember()'ing, or stale beliefs to supersede via remember(supersedes=<old_id>) per §13.18 D1?"
  jq -nc --arg m "$msg" '{systemMessage: $m}'
fi

exit 0
