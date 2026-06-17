"""Tests for log rotation/retention + segment-aware reading (Track I).

Rotation bounds the live append-only log by archiving it to numbered segments; ``read_jsonl``
concatenates the segments oldest-first so retained history is read back whole; ``keep`` drops the
oldest archives beyond the retention window."""

from __future__ import annotations

from pathlib import Path

from thalamus.instrumentation import jsonl_segments, read_jsonl, rotate_log
from thalamus.instrumentation._jsonl import append_jsonl


def _write(path: Path, n: int, tag: str) -> None:
    for i in range(n):
        append_jsonl(path, {"tag": tag, "i": i})


def test_no_rotation_below_threshold(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    _write(log, 3, "a")
    assert rotate_log(log, max_bytes=10_000, keep=8) is False
    assert jsonl_segments(log) == [log]


def test_rotation_archives_and_starts_fresh(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    _write(log, 50, "a")
    assert rotate_log(log, max_bytes=10, keep=8) is True
    assert (tmp_path / "x.jsonl.1").exists()
    assert not log.exists()  # rotated away; the next append recreates a fresh live file
    # the archived data is still readable through read_jsonl (rotation is invisible to readers)
    assert [r["i"] for r in read_jsonl(log)] == list(range(50))


def test_read_jsonl_concatenates_segments_oldest_first(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    _write(log, 5, "old")
    rotate_log(log, max_bytes=10, keep=8)  # old → .1
    _write(log, 5, "new")  # a fresh live file
    rows = list(read_jsonl(log))
    assert [r["tag"] for r in rows] == ["old"] * 5 + ["new"] * 5  # archive first, then live


def test_keep_drops_oldest_beyond_window(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    for round_ in range(4):
        _write(log, 20, f"r{round_}")
        rotate_log(log, max_bytes=10, keep=2)
    indices = sorted(int(p.name.rsplit(".", 1)[1]) for p in tmp_path.glob("x.jsonl.*"))
    assert indices == [1, 2]  # only two archives retained; the two oldest rounds were dropped
    tags = {r["tag"] for r in read_jsonl(log)}
    assert tags == {"r2", "r3"}  # the retained window, read back whole


def test_max_bytes_zero_disables_rotation(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    _write(log, 50, "a")
    assert rotate_log(log, max_bytes=0, keep=8) is False
    assert jsonl_segments(log) == [log]
