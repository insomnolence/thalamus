"""Co-change coupling — symbols that historically change together (a logical-coupling signal).

The call graph captures *structural* coupling (A calls B), but the impact eval showed most real
change-coupling is **not** call-adjacent: things get fixed together because they're the same
feature across files, share a data shape, or are conceptually linked — coupling no call edge ever
had. Co-change is that signal, read deterministically from git: when a commit touches symbols A and
B together, their coupling count goes up. It is firewall-clean — a behavioural *act* (developers
changed them together), never the model judging its own output.

This is a removable layer over the call-graph blast radius (§14): the planner fuses it when given a
:class:`CoChangeIndex` and is unchanged without one. **Anti-circularity is the caller's job** —
when *measuring* the lift, build the index from commits strictly older than the evaluated ones (a
temporal split), or the radius trivially "predicts" the co-change it was built from.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Protocol, runtime_checkable

from thalamus.core.types import StructuralRef


@runtime_checkable
class CoChangeIndex(Protocol):
    """Symbol → the symbols it has historically co-changed with, by descending count."""

    def cochanged(self, ref: StructuralRef) -> list[tuple[StructuralRef, int]]: ...


class InMemoryCoChangeIndex:
    """Accumulates symmetric pairwise co-change counts from commits (each = a set of symbols)."""

    def __init__(self) -> None:
        self._counts: dict[StructuralRef, dict[StructuralRef, int]] = {}

    def __len__(self) -> int:
        return len(self._counts)

    def _bump(self, a: StructuralRef, b: StructuralRef) -> None:
        row = self._counts.setdefault(a, {})
        row[b] = row.get(b, 0) + 1

    def add_commit(self, refs: Iterable[StructuralRef]) -> None:
        """Record one commit's co-change: every distinct pair of touched symbols +1 (both ways)."""
        unique = list(dict.fromkeys(refs))  # de-dup, preserve order
        for i, a in enumerate(unique):
            for b in unique[i + 1 :]:
                self._bump(a, b)
                self._bump(b, a)

    def cochanged(self, ref: StructuralRef) -> list[tuple[StructuralRef, int]]:
        partners = self._counts.get(ref)
        if not partners:
            return []
        return sorted(partners.items(), key=lambda kv: kv[1], reverse=True)


class FileCoChangeIndex:
    """Co-change at FILE granularity, surfaced as symbol partners — the drift-immune variant.

    Symbol-level co-change is starved when commits are mapped onto a single (HEAD) index: old
    diffs land on moved/renamed symbols. File *paths* are stable across revisions, so file-level
    co-change needs no anchor mapping and doesn't drift (the classic Zimmermann result is
    file-granular). This index accumulates file↔file co-change, then answers the symbol-level
    :class:`CoChangeIndex` query by expanding a symbol → its file → co-changed files → the symbols
    that live in them. The planner consumes it through the same seam, unchanged.

    ``ref_file``/``file_refs`` are the symbol↔file membership (from the current graph's anchors),
    injected once; ``add_commit`` takes a commit's changed *file paths* (drift-immune)."""

    def __init__(
        self,
        ref_file: Mapping[StructuralRef, str],
        file_refs: Mapping[str, Sequence[StructuralRef]],
    ) -> None:
        self._ref_file = dict(ref_file)
        self._file_refs = {path: list(refs) for path, refs in file_refs.items()}
        self._file_partners: dict[str, dict[str, int]] = {}

    def __len__(self) -> int:
        return len(self._file_partners)

    def _bump(self, a: str, b: str) -> None:
        row = self._file_partners.setdefault(a, {})
        row[b] = row.get(b, 0) + 1

    def add_commit(self, files: Iterable[str]) -> None:
        """Record one commit's co-change: every distinct pair of changed files +1 (both ways)."""
        unique = list(dict.fromkeys(files))
        for i, a in enumerate(unique):
            for b in unique[i + 1 :]:
                self._bump(a, b)
                self._bump(b, a)

    def cochanged(self, ref: StructuralRef) -> list[tuple[StructuralRef, int]]:
        """Symbols in files that co-changed with ``ref``'s file, scored by the file-pair count."""
        home = self._ref_file.get(ref)
        if home is None:
            return []
        partners = self._file_partners.get(home)
        if not partners:
            return []
        best: dict[StructuralRef, int] = {}
        for partner_file, count in partners.items():
            for symbol in self._file_refs.get(partner_file, ()):
                if symbol == ref:
                    continue
                prior = best.get(symbol)
                if prior is None or count > prior:
                    best[symbol] = count
        return sorted(best.items(), key=lambda kv: kv[1], reverse=True)
