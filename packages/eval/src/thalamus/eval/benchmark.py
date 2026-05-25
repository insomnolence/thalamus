"""Benchmark cases for retrieval evaluation.

A case is a cue plus the set of memory ids that *should* be surfaced. The curated
benchmark is the frozen regression guard (OLR §13.20); a JSONL loader keeps it
external and editable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from thalamus.core.types import Cue, MemoryId, Scope


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    cue: Cue
    relevant: frozenset[MemoryId]


def load_cases(path: Path, scope: Scope) -> list[BenchmarkCase]:
    """Load cases from newline-delimited JSON: ``{"query": ..., "relevant": [ids]}``."""
    cases: list[BenchmarkCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        obj = json.loads(stripped)
        relevant = frozenset(MemoryId(str(memory_id)) for memory_id in obj["relevant"])
        cases.append(BenchmarkCase(cue=Cue(text=str(obj["query"]), scope=scope), relevant=relevant))
    return cases
