"""Grounded outcome-event extraction from a memory's text — a MINOR, model-arbitrated input to
fate-based credibility (OLR §13.8/§13.10; see docs/deep-dives/dreaming.md, "Pass: fate-based
credibility").

Author- and agent-count-agnostic: a single agent's "had to redo X" is the same signal as a
reviewer's "pushed back (NOT committed)" — the dev/reviewer setup is one *dialect*, not the design.

**Firewall (§13.7).** Key on the recorded **action** (committed / reverted / redone / not-
committed), NEVER on quality adjectives ("good idea", "well written") — a model's opinion must
never become an outcome label. The text is the *discovery* channel; the act licenses the label.
Because committing is not validated success (the project excludes a bare ``COMMITTED``), the
valuable contribution here is the **negative** (``UNDONE``); ``LANDED`` is kept only to
*disambiguate* (so "committed after rework" is not misread as undone) and for future difficulty
grading. Conservative: no grounded action → None.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_SHA_RE = re.compile(r"committed (?:as|in) ([0-9a-f]{7,40})\b")

# The work LANDED (a positive *act*, not a validated success): an explicit commit verb, or — one
# recognised dialect — a clean review acceptance.
_LANDED_STRONG = ("committed as", "committed in", "and committed", "re-reviewed and committed")
_LANDED_SOFT = ("committing", "landed", "shipped", "merged")  # require a review context to count

# The work was UNDONE / redone (the valuable negative act). Action verbs only — never bare
# "was wrong" / "didn't work" (those are opinion-adjacent without the grounding act).
_UNDONE = (
    "reverted",
    "rolled back",
    "rolled-back",
    "backed out",
    "had to redo",
    "had to rewrite",
    "redid",
    "not committed",
)
_PUSHED_BACK = ("pushed back", "pushback", "push-back")


class RecordedEvent(StrEnum):
    """A grounded outcome event extracted from memory text."""

    LANDED = "landed"  # committed / shipped / clean-reviewed — a positive *act*, not proven success
    UNDONE = "undone"  # reverted / redone / not-committed — the valuable negative act


@dataclass(frozen=True, slots=True)
class TextOutcome:
    """An extracted event with its commit sha (when named) and the phrase that triggered it."""

    event: RecordedEvent
    sha: str | None
    evidence: str


def _extract_sha(text_lower: str) -> str | None:
    match = _SHA_RE.search(text_lower)
    return match.group(1) if match else None


def parse_recorded_outcome(text: str) -> TextOutcome | None:
    """Extract a grounded outcome event from a memory's text, or ``None``.

    ``LANDED`` dominates ``UNDONE`` markers (work committed *after* rework still landed). A clean
    review acceptance counts as ``LANDED`` only via the explicit "reviewed clean" / "no push-back"
    phrases or a soft commit verb in a review context — so "kernel projection **clean**up" and
    "the function **commit**s to the database" are not false positives. Returns ``None`` when no
    grounded action phrase is present (missing data, never a guessed outcome)."""
    tl = text.lower()
    sha = _extract_sha(tl)
    no_pushback = "no push-back" in tl or "no pushback" in tl

    landed = sha is not None or any(marker in tl for marker in _LANDED_STRONG)
    if not landed and any(marker in tl for marker in _LANDED_SOFT):
        landed = "review" in tl
    if not landed and ("reviewed clean" in tl or no_pushback):
        landed = True
    if landed:
        return TextOutcome(RecordedEvent.LANDED, sha, _landed_evidence(tl, sha))

    undone = any(marker in tl for marker in _UNDONE) or (
        not no_pushback and any(marker in tl for marker in _PUSHED_BACK)
    )
    if undone:
        return TextOutcome(RecordedEvent.UNDONE, None, _undone_evidence(tl))
    return None


def _landed_evidence(text_lower: str, sha: str | None) -> str:
    if sha is not None:
        return f"committed {sha}"
    for phrase in (*_LANDED_STRONG, *_LANDED_SOFT, "reviewed clean", "no push-back"):
        if phrase in text_lower:
            return phrase
    return "clean review"


def _undone_evidence(text_lower: str) -> str:
    for phrase in (*_UNDONE, *_PUSHED_BACK):
        if phrase in text_lower:
            return phrase
    return "undone"
