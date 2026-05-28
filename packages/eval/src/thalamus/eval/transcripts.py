"""Transcript probe extraction — Claude Code session transcripts as a probe corpus.

The L1-on-real-data eval source. Every Claude Code session writes a JSONL transcript
of every record (user, assistant, tool_use, tool_result, …) under
``~/.claude/projects/<sanitized-cwd>/<session-uuid>.jsonl``. The substantive *user*
prompts in those transcripts are real questions the actuator asked the brain to help
with; replaying them against the current Gateway answers **"would the brain have
surfaced anything useful on the real questions"** — the L1 number we can compute
without an outcome-volume controlled A/B.

No ground-truth labels (transcripts don't carry them); the eval runner in
:mod:`thalamus.eval.probe` scores unlabeled probes via surface-rate + mean top-1
relevance + ablation against a brain-off baseline. The L3 verdict still requires real
outcomes — this is strictly an L1 instrument.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TranscriptProbe:
    """One substantive user prompt extracted from a Claude Code transcript."""

    prompt: str
    session_id: str
    timestamp: datetime
    cwd: str | None = None
    git_branch: str | None = None


def extract_probes(
    transcript_paths: Iterable[Path], *, min_length: int = 20
) -> list[TranscriptProbe]:
    """Extract substantive user prompts from a list of transcript JSONL files.

    A "substantive" prompt is a *string-content* user message of at least
    ``min_length`` characters that doesn't start with ``<`` — that filter excludes
    tool-result records (lists of content blocks), slash-command blocks like
    ``<command-name>...`` and ``<local-command-caveat>...``, and ``<system-reminder>``
    blocks the harness injects as user records. Order is the order paths are passed;
    within a file, the transcript's own order is preserved.
    """
    probes: list[TranscriptProbe] = []
    for path in transcript_paths:
        probes.extend(_extract_from_file(path, min_length))
    return probes


def _extract_from_file(path: Path, min_length: int) -> Iterator[TranscriptProbe]:
    session_id = path.stem
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # skip malformed records, don't fail the whole transcript
            prompt = _substantive_prompt(record, min_length)
            if prompt is None:
                continue
            yield TranscriptProbe(
                prompt=prompt,
                session_id=session_id,
                timestamp=_parse_timestamp(record.get("timestamp")),
                cwd=_optional_str(record.get("cwd")),
                git_branch=_optional_str(record.get("gitBranch")),
            )


def _substantive_prompt(record: object, min_length: int) -> str | None:
    if not isinstance(record, dict):
        return None
    if record.get("type") != "user":
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str):
        # Tool-result records carry a list of content blocks — not a user question.
        return None
    text = content.strip()
    if len(text) < min_length:
        return None
    if text.startswith("<"):
        # Excludes slash-command blocks (<command-name>...) and harness-injected
        # <system-reminder>/<local-command-caveat> records.
        return None
    return text


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.fromtimestamp(0, tz=UTC)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def default_transcripts_dir(cwd: Path | None = None) -> Path:
    """Default Claude Code transcript directory for ``cwd`` (defaults to the current cwd).

    Claude Code stores transcripts under ``~/.claude/projects/<sanitized-cwd>/`` where
    the sanitization replaces ``/`` with ``-`` and prefixes a leading ``-``.
    """
    cwd = (cwd or Path.cwd()).resolve()
    sanitized = "-" + str(cwd).strip("/").replace("/", "-")
    return Path.home() / ".claude" / "projects" / sanitized


def find_transcripts(directory: Path | None = None) -> list[Path]:
    """Return all ``*.jsonl`` transcripts under ``directory`` (default: the project's)."""
    directory = directory or default_transcripts_dir()
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.jsonl"))
