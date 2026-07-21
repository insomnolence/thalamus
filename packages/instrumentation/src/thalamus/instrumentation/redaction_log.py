"""Redaction telemetry — an auditable, secret-free record of what the scrubber removed (§17.4 T2).

The secret scrubber (``core.redaction``) redacts at ingest, but a redaction is invisible by design:
the secret is gone and only ``[REDACTED:<kind>]`` remains. To make coverage *auditable* (is it
firing? which shapes? is something new slipping through?), each redaction appends one event here: a
timestamp, the source boundary (``remember`` / ``episode`` / …), and per-kind counts — **never the
secret text** (logging it would re-open the leak we just closed). ``verdict`` reads + summarizes it.

A JSONL sibling of the other telemetry logs (``retrieval.jsonl`` / ``usage.jsonl``), so it rotates
with them and is read back the same way. Written only where a logs dir exists (the serve's remember
and capture paths); a redaction always still scrubs and stamps the record metadata even if unlogged.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from thalamus.core.redaction import RedactionEvent, merge_redaction_events
from thalamus.instrumentation._jsonl import append_jsonl, read_jsonl


def redaction_events_from_metadata(metadata: Mapping[str, Any]) -> list[RedactionEvent]:
    """Read the ``metadata["redacted"]`` audit list (``[{"kind","count"}, …]``) a record carries
    after scrubbing back into :class:`RedactionEvent` objects (empty when the key is absent).

    The adapter between the stamp the builders leave on a record and the telemetry log writer, so
    the serve write sites don't each re-parse the convention."""
    raw = metadata.get("redacted")
    if not isinstance(raw, list):
        return []
    events: list[RedactionEvent] = []
    for entry in raw:
        if isinstance(entry, Mapping) and "kind" in entry and "count" in entry:
            events.append(RedactionEvent(str(entry["kind"]), int(entry["count"])))
    return events


def append_redaction(
    path: Path,
    source: str,
    events: Iterable[RedactionEvent],
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Append one redaction event (no-op if ``events`` empty). Records kind→count, no secret."""
    merged = merge_redaction_events(events)
    if not merged:
        return
    append_jsonl(
        path,
        {
            "at": now().isoformat(),
            "source": source,
            "counts": {event.kind: event.count for event in merged},
        },
    )


@dataclass(frozen=True, slots=True)
class RedactionSummary:
    """Aggregate of a redaction log: total secrets removed, event count, per-kind breakdown."""

    total: int = 0
    events: int = 0
    by_kind: Mapping[str, int] = field(default_factory=dict)


def summarize_redaction_log(path: Path) -> RedactionSummary:
    """Read + aggregate ``path`` (a missing file → an empty summary)."""
    if not path.exists():
        return RedactionSummary()
    total = 0
    events = 0
    by_kind: dict[str, int] = {}
    for row in read_jsonl(path):
        counts = row.get("counts")
        if not isinstance(counts, dict):
            continue
        events += 1
        for kind, count in counts.items():
            n = int(count)
            total += n
            by_kind[str(kind)] = by_kind.get(str(kind), 0) + n
    return RedactionSummary(total=total, events=events, by_kind=dict(sorted(by_kind.items())))
