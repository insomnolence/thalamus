"""Shared newline-delimited-JSON read/append helpers for the append-only logs."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from thalamus.instrumentation.rotation import jsonl_segments


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    """Append one JSON object as a line to ``path`` (creating parents)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(obj, separators=(",", ":")) + "\n")


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield each non-blank line parsed as a JSON object (the inverse of :func:`append_jsonl`).

    Reads across ``path``'s rotated segments oldest-first (:func:`jsonl_segments`), so a log split
    by retention rotation is read back as one continuous history. Streams lazily so large logs need
    not fit in memory."""
    for segment in jsonl_segments(path):
        with segment.open(encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    yield json.loads(stripped)
