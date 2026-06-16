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
    """Symbol → the symbols it has historically co-changed with, by descending *coupling score*."""

    def cochanged(self, ref: StructuralRef) -> list[tuple[StructuralRef, float]]: ...


class InMemoryCoChangeIndex:
    """Accumulates symmetric pairwise co-change counts from commits (each = a set of symbols).

    The symbol-level scaffold (drift-starved on a single HEAD index — see :class:`FileCoChangeIndex`
    for the variant that works). Scores partners by raw co-occurrence count."""

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

    def cochanged(self, ref: StructuralRef) -> list[tuple[StructuralRef, float]]:
        partners = self._counts.get(ref)
        if not partners:
            return []
        scored = [(other, float(count)) for other, count in partners.items()]
        return sorted(scored, key=lambda kv: kv[1], reverse=True)


class FileCoChangeIndex:
    """Co-change at FILE granularity, surfaced as symbol partners — the drift-immune variant.

    Symbol-level co-change is starved when commits are mapped onto a single (HEAD) index: old
    diffs land on moved/renamed symbols. File *paths* are stable across revisions, so file-level
    co-change needs no anchor mapping and doesn't drift (the classic Zimmermann result is
    file-granular). This index accumulates file↔file co-change, then answers the symbol-level
    :class:`CoChangeIndex` query by expanding a symbol → its file → co-changed files → the symbols
    that live in them. The planner consumes it through the same seam, unchanged.

    **Scored by lift, not raw count.** Raw co-occurrence over-credits *hub/sweep* files (a file
    touched in a large fraction of commits "co-changes" with everything). ``lift = P(both) /
    (P(home)·P(partner))`` divides out the partner's base frequency, so a partner that changes
    everywhere scores ~1 (chance) and real coupling scores high. A ``min_cooccur`` support gate
    drops one-off coincidences, and ``max_file_frequency`` hard-excludes files appearing in more
    than that fraction of commits (sweeps — formatting passes, big renames) as partners.

    ``ref_file``/``file_refs`` are the symbol↔file membership (from the current graph's anchors),
    injected once; ``add_commit`` takes a commit's changed *file paths* (drift-immune)."""

    def __init__(
        self,
        ref_file: Mapping[StructuralRef, str],
        file_refs: Mapping[str, Sequence[StructuralRef]],
        *,
        min_cooccur: int = 2,
        max_file_frequency: float = 0.10,
    ) -> None:
        self._ref_file = dict(ref_file)
        self._file_refs = {path: list(refs) for path, refs in file_refs.items()}
        self._file_partners: dict[str, dict[str, int]] = {}
        self._file_commits: dict[str, int] = {}  # commits each file appeared in (the base rate)
        self._commits = 0
        self._min_cooccur = min_cooccur
        self._max_file_frequency = max_file_frequency

    def __len__(self) -> int:
        return len(self._file_partners)

    def _bump(self, a: str, b: str) -> None:
        row = self._file_partners.setdefault(a, {})
        row[b] = row.get(b, 0) + 1

    def add_commit(self, files: Iterable[str]) -> None:
        """Record one commit: per-file base rates + every distinct pair of changed files (both)."""
        unique = list(dict.fromkeys(files))
        if not unique:
            return
        self._commits += 1
        for path in unique:
            self._file_commits[path] = self._file_commits.get(path, 0) + 1
        for i, a in enumerate(unique):
            for b in unique[i + 1 :]:
                self._bump(a, b)
                self._bump(b, a)

    def cochanged(self, ref: StructuralRef) -> list[tuple[StructuralRef, float]]:
        """Symbols in files coupled to ``ref``'s file, scored by lift (hub files filtered out)."""
        home = self._ref_file.get(ref)
        if home is None:
            return []
        partners = self._file_partners.get(home)
        home_commits = self._file_commits.get(home, 0)
        if not partners or home_commits == 0 or self._commits == 0:
            return []
        freq_cap = self._max_file_frequency * self._commits
        best: dict[StructuralRef, float] = {}
        for partner_file, cooccur in partners.items():
            partner_commits = self._file_commits.get(partner_file, 0)
            if cooccur < self._min_cooccur or partner_commits == 0 or partner_commits > freq_cap:
                continue  # one-off, or a sweep/hub file that co-changes with everything
            lift = cooccur * self._commits / (home_commits * partner_commits)
            for symbol in self._file_refs.get(partner_file, ()):
                if symbol == ref:
                    continue
                prior = best.get(symbol)
                if prior is None or lift > prior:
                    best[symbol] = lift
        return sorted(best.items(), key=lambda kv: kv[1], reverse=True)


class CoChangeRef:
    """A single-slot holder that *is* a :class:`CoChangeIndex`: the live planner reads it, and a
    dreaming pass swaps a freshly-built index in mid-serve without a restart.

    Mirrors :class:`~thalamus.gateway.views.DerivedViewsRef` / ``UsageWeightsRef``: the swap is a
    single attribute store (atomic under the GIL), and :meth:`cochanged` snapshots the current
    index once per call into a local, so a concurrent refresh is observed whole, never torn."""

    def __init__(self, index: CoChangeIndex | None = None) -> None:
        self._index = index

    def refresh(self, index: CoChangeIndex) -> None:
        """Atomically replace the current index (one ``STORE_ATTR``)."""
        self._index = index

    def cochanged(self, ref: StructuralRef) -> list[tuple[StructuralRef, float]]:
        index = self._index  # snapshot once: consistent across this call
        return index.cochanged(ref) if index is not None else []
