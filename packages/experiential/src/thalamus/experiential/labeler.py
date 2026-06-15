"""The negative-signal labeler — survival-vs-overwrite fate, region-precise (Step 1, learning-loop).

⚠️ PARKED (2026-06-15) — DORMANT, CANDIDATE FOR REMOVAL. This is part of the *outcome*-credibility
loop, which was re-aimed away from (see ROADMAP.md Track L / retained:dc134e4a): the learning target
is now RELEVANCE credibility (usage + supersession + recency), which accrues in the actual workflow,
whereas this code's signal (committed-code survival) needs instrumented coding that doesn't happen
here. Kept, tested, correct — wakes up *if* heavy instrumented coding resumes; otherwise remove.


In a fix-forward workflow ``git revert`` ~never fires, so the negative is the **rewrite, not the
revert**: a dead-end is spelled as *churn* in the diff. This labeler reads each commit-episode's
fate from git — how much of the code it introduced **survived** at HEAD vs was **overwritten** — and
fills the two :class:`~thalamus.experiential.fate.FateContext` fields that are otherwise stubbed
empty (``churn_ratio`` / ``survived_activity``).

Region-precise: for an ``episode:<sha>`` memory the "region" is the exact set of lines that commit
introduced; ``git blame`` at HEAD tells us how many still survive. **Exercised survival** — later
activity only counts when it *touched the footprint* (a region nobody revisited "surviving" is weak
evidence; we credit survival only against commits that actually exercised the area).

Split like the rest of fate.py: :func:`region_fate` is the pure, unit-testable combiner; the git
archaeology lives behind the :class:`OutcomeLabeler` seam so the heuristic is swappable (a better
diff model or an LLM-judge-with-firewall drops in without touching downstream). Curated
(``retained:…``) memories have no introduced region, so they are skipped here — their credibility
comes from supersession + reuse (see :func:`~thalamus.experiential.fate.fate_signals_for`).
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from thalamus.core.types import MemoryId, MemoryRecord
from thalamus.experiential.fate import _DEFAULT_CHURN_THRESHOLD, _episode_sha


@dataclass(frozen=True, slots=True)
class FateLabels:
    """Per-memory churn/survival signals — the labeler's output, fed into ``FateContext``."""

    churn_ratio: Mapping[MemoryId, float] = field(default_factory=dict)
    survived_activity: Mapping[MemoryId, int] = field(default_factory=dict)


@runtime_checkable
class OutcomeLabeler(Protocol):
    """Produces churn/survival fate signals for memories from external history. Swappable (§14)."""

    def label(self, memories: Sequence[MemoryRecord]) -> FateLabels:
        """Return per-memory churn/survival, reading only external facts (the firewall)."""
        ...


def region_fate(
    *,
    introduced: int,
    surviving: int,
    exercising_commits: int,
    churn_threshold: float = _DEFAULT_CHURN_THRESHOLD,
) -> tuple[float, int]:
    """Combine one region's git facts into ``(churn_ratio, survived_activity)``. Pure.

    - ``introduced`` — lines the commit added in its footprint.
    - ``surviving`` — how many of those still exist at HEAD (``git blame`` attribution).
    - ``exercising_commits`` — later commits that *touched the footprint* (the activity the region
      had to withstand; "exercised survival").

    ``churn_ratio = 1 - surviving/introduced`` (clamped ``[0, 1]``; ``0.0`` when nothing was
    introduced — no evidence, not "clean"). ``survived_activity`` credits the exercising commits
    **only if the region actually held** (churn below threshold); a churned-away region survived
    nothing, so it returns ``0`` and lets ``churn_ratio`` carry the negative signal. The two never
    both fire positive."""
    if introduced <= 0:
        return 0.0, 0
    held = max(0, min(surviving, introduced))
    churn = 1.0 - held / introduced
    survived = exercising_commits if churn < churn_threshold else 0
    return churn, survived


class GitSurvivalLabeler:
    """An :class:`OutcomeLabeler` that reads survival/churn from git for commit-episodes.

    For each ``episode:<sha>`` memory it asks git three things over the memory's footprint files:
    how many lines ``<sha>`` introduced, how many survive at HEAD (blame), and how many later
    commits touched the footprint. Missing data (a sha not in HEAD's history, a deleted/binary file,
    a non-repo path) is *skipped*, never fatal — the same down-weight-don't-fabricate discipline as
    the segmenters. I/O-heavy (a blame per footprint file); runs in the dreaming pass, not on
    recall."""

    def __init__(
        self, repo_path: Path, *, churn_threshold: float = _DEFAULT_CHURN_THRESHOLD
    ) -> None:
        self._repo = repo_path
        self._churn_threshold = churn_threshold

    def _git(self, *args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(self._repo), *args],
                capture_output=True,
                text=True,
                # Blame reads file *contents*, which may be binary/non-utf8 — replace undecodable
                # bytes rather than crash; the sha headers we count are ASCII, so counts hold.
                errors="replace",
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        return result.stdout

    def _footprint(self, memory: MemoryRecord) -> list[str]:
        raw = memory.metadata.get("footprint", ())
        return [str(path) for path in raw] if isinstance(raw, (list, tuple)) else []

    def _introduced(self, sha: str, files: Sequence[str]) -> int:
        """Lines ``sha`` added across ``files`` (``git show --numstat``; ``-`` = binary → 0)."""
        out = self._git("show", "--numstat", "--format=", sha, "--", *files)
        if out is None:
            return 0
        total = 0
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 1 and parts[0].isdigit():
                total += int(parts[0])
        return total

    def _surviving(self, sha: str, files: Sequence[str]) -> int:
        """Lines at HEAD that ``git blame`` still attributes to ``sha`` (i.e. never overwritten)."""
        prefix = sha + " "
        surviving = 0
        for path in files:
            out = self._git("blame", "--line-porcelain", "HEAD", "--", path)
            if out is None:  # file gone at HEAD, or never tracked → its lines didn't survive
                continue
            surviving += sum(1 for line in out.splitlines() if line.startswith(prefix))
        return surviving

    def _exercising_commits(self, sha: str, files: Sequence[str]) -> int:
        """Commits after ``sha`` that touched the footprint files (the activity it withstood)."""
        out = self._git("log", "--format=%H", f"{sha}..HEAD", "--", *files)
        if out is None:
            return 0
        return sum(1 for line in out.splitlines() if line.strip())

    def commit_line_stats(
        self, commits: Sequence[tuple[str, Sequence[str]]]
    ) -> dict[str, tuple[int, int]]:
        """Per-commit ``(introduced, surviving)`` line counts over each commit's files — the raw
        inputs for *session-level* survival aggregation (``verdict.session_fate`` sums a session's
        own commits, then applies :func:`region_fate`). Commits with nothing introduced (sha not in
        HEAD's history, or no added lines) are omitted (missing data, not zero)."""
        stats: dict[str, tuple[int, int]] = {}
        for sha, raw_files in commits:
            files = list(raw_files)
            if not files:
                continue
            introduced = self._introduced(sha, files)
            if introduced <= 0:
                continue
            stats[sha] = (introduced, self._surviving(sha, files))
        return stats

    def label(self, memories: Sequence[MemoryRecord]) -> FateLabels:
        churn: dict[MemoryId, float] = {}
        survived: dict[MemoryId, int] = {}
        for memory in memories:
            sha = _episode_sha(memory.memory_id)
            files = self._footprint(memory)
            if sha is None or not files:  # curated / footprint-less → no region to track here
                continue
            introduced = self._introduced(sha, files)
            if introduced <= 0:  # sha not in HEAD's history, or nothing added → no evidence
                continue
            ratio, survived_n = region_fate(
                introduced=introduced,
                surviving=self._surviving(sha, files),
                exercising_commits=self._exercising_commits(sha, files),
                churn_threshold=self._churn_threshold,
            )
            churn[memory.memory_id] = ratio
            survived[memory.memory_id] = survived_n
        return FateLabels(churn_ratio=churn, survived_activity=survived)
