"""Retained-memory kind taxonomy + synonym normalization.

The five canonical curated kinds — decision / constraint / gotcha / investigation /
preference — are the vocabulary the rest of the system stores and switches on. Actuators
built on Claude Code instead reach for *their own* memory taxonomy (user / feedback /
project / reference), so a small synonym map normalizes those to the canonical set rather
than losing the write (§14.4: losing the memory is worse than a mislabeled kind — the same
principle the footprint skip already follows). A kind outside the accepted set is still
rejected — cleanly, at the schema boundary — so a genuinely novel label is a *visible*
error the caller can recover from, never a silent coercion into a wrong bucket.
"""

from __future__ import annotations

from typing import Literal, get_args

from thalamus.core.exceptions import ThalamusError

RetainedKind = Literal["decision", "constraint", "gotcha", "investigation", "preference"]
"""The canonical curated-memory kinds — the only values stored and switched on."""

# Claude Code's native memory taxonomy (user / feedback / project / reference) leaks into
# remember calls; map each to its nearest canonical home so the write survives on-vocabulary.
# `reference` has no exact peer — `investigation` (recorded looked-up knowledge) is the closest.
KIND_SYNONYMS: dict[str, str] = {
    "project": "decision",
    "user": "preference",
    "feedback": "preference",
    "reference": "investigation",
}

RememberKindInput = Literal[
    "decision",
    "constraint",
    "gotcha",
    "investigation",
    "preference",
    "project",
    "user",
    "feedback",
    "reference",
]
"""Every kind a caller may *send*: the canonical set plus the accepted synonyms. The MCP
tool and CLI advertise this so an unknown kind is rejected at the boundary with the full
list, not as an uncaught error deep in record construction."""

RETAINED_KINDS: tuple[str, ...] = get_args(RetainedKind)
ACCEPTED_KINDS: tuple[str, ...] = get_args(RememberKindInput)


def normalize_kind(kind: str) -> str:
    """Canonicalize a caller-supplied kind.

    A canonical kind passes through unchanged; a known synonym maps to its canonical home;
    anything else raises :class:`ThalamusError` (the clean, recoverable error listing the
    canonical kinds and accepted synonyms)."""
    canonical = KIND_SYNONYMS.get(kind, kind)
    if canonical not in RETAINED_KINDS:
        raise ThalamusError(
            f"unsupported retained memory kind: {kind!r}; expected one of "
            f"{', '.join(RETAINED_KINDS)} (or a synonym: {', '.join(KIND_SYNONYMS)})"
        )
    return canonical
