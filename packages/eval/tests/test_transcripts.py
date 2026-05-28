"""Transcript probe extraction — filters substantive user prompts out of the
Claude Code transcript JSONL."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from thalamus.eval.transcripts import (
    default_transcripts_dir,
    extract_probes,
    find_transcripts,
)


def _write_transcript(path: Path, records: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def _user(content: object, *, ts: str = "2026-05-28T10:00:00Z", cwd: str | None = None) -> dict:
    record: dict[str, object] = {
        "type": "user",
        "message": {"role": "user", "content": content},
        "timestamp": ts,
    }
    if cwd is not None:
        record["cwd"] = cwd
    return record


def test_extracts_real_user_questions(tmp_path: Path) -> None:
    transcript = tmp_path / "abc.jsonl"
    _write_transcript(transcript, [
        _user("can you explain how the retrieval chain composes?", cwd="/home/dibble/thalamus"),
        _user("yes proceed"),  # short → excluded
        _user(  # exactly 20 chars including the question mark and spaces → kept
            "how do we ablate it?"
        ),
    ])
    probes = extract_probes([transcript])
    prompts = [p.prompt for p in probes]
    assert prompts == [
        "can you explain how the retrieval chain composes?",
        "how do we ablate it?",
    ]
    assert probes[0].session_id == "abc"
    assert probes[0].cwd == "/home/dibble/thalamus"
    assert probes[0].timestamp == datetime(2026, 5, 28, 10, 0, tzinfo=UTC)


def test_skips_tool_result_records(tmp_path: Path) -> None:
    transcript = tmp_path / "tools.jsonl"
    _write_transcript(transcript, [
        # The harness writes tool_result back into the session as type=user but with a
        # *list* content — not a real user question.
        _user([{"type": "tool_result", "content": "x" * 200, "tool_use_id": "t1"}]),
        _user("actual question that is plenty long to count"),
    ])
    prompts = [p.prompt for p in extract_probes([transcript])]
    assert prompts == ["actual question that is plenty long to count"]


def test_skips_slash_commands_and_system_reminders(tmp_path: Path) -> None:
    transcript = tmp_path / "cmds.jsonl"
    _write_transcript(transcript, [
        _user("<command-name>/mcp</command-name>\n<command-message>mcp</command-message>"),
        _user("<local-command-caveat>Caveat about local commands</local-command-caveat>"),
        _user("<system-reminder>Session reminder text injected by the harness</system-reminder>"),
        _user("real question after the reminders — should be kept"),
    ])
    prompts = [p.prompt for p in extract_probes([transcript])]
    assert prompts == ["real question after the reminders — should be kept"]


def test_skips_malformed_lines_without_failing(tmp_path: Path) -> None:
    transcript = tmp_path / "bad.jsonl"
    transcript.write_text(
        "not valid json\n"
        + json.dumps(_user("good question that is well over the threshold")) + "\n"
        + "\n"  # blank line
        + "{also broken\n",
        encoding="utf-8",
    )
    prompts = [p.prompt for p in extract_probes([transcript])]
    assert prompts == ["good question that is well over the threshold"]


def test_min_length_is_configurable(tmp_path: Path) -> None:
    transcript = tmp_path / "short.jsonl"
    _write_transcript(transcript, [
        _user("short question?"),  # 15 chars
        _user("medium question here"),  # 20 chars
    ])
    assert [p.prompt for p in extract_probes([transcript], min_length=10)] == [
        "short question?", "medium question here",
    ]
    assert [p.prompt for p in extract_probes([transcript], min_length=20)] == [
        "medium question here"
    ]


def test_find_transcripts_returns_sorted_jsonl(tmp_path: Path) -> None:
    (tmp_path / "z.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "a.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "ignore.txt").write_text("", encoding="utf-8")
    found = find_transcripts(tmp_path)
    assert [p.name for p in found] == ["a.jsonl", "z.jsonl"]


def test_default_transcripts_dir_uses_sanitized_cwd(tmp_path: Path) -> None:
    expected_suffix = "-" + str(tmp_path).strip("/").replace("/", "-")
    resolved = default_transcripts_dir(tmp_path)
    assert resolved.name == expected_suffix
    assert resolved.parent.name == "projects"
