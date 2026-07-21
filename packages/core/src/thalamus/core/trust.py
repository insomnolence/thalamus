"""Provenance / trust levels for content that enters the brain (§17.4 step 1 — threats T1/T3).

Thalamus fuses its corpora back into the actuator's prompt on every recall, so *who authored* a
piece of content matters: the operator's own repo is trusted; a vendored dependency tree, an
external doc set, or a third-party analysis producer is **not** — instruction-shaped text in it
(``ignore previous instructions and…``) must reach the actuator as *data about the world*, not as
*instructions to follow* (T1), and must not, on its own, out-rank operator-authored memory (T3).

Trust is keyed on the **producer / corpus**, not promoted per entry (§17.4): a ``[[corpus]]`` sets
its trust once and every node it yields inherits it. The default is :attr:`Trust.OPERATOR` — the
single-operator brain over its own repos, today's only configuration — so nothing changes until a
corpus is explicitly declared otherwise. The recall-path fence (``gateway/payload.py``) reads this
to decide what to wrap; the (deferred) credibility layer (§17.4 step 4) will read it to cap ranking.
"""

from __future__ import annotations

from enum import StrEnum


class Trust(StrEnum):
    """How much the brain trusts a piece of content, by its producer/corpus provenance."""

    OPERATOR = "operator"  # authored by the operator (own repo, notes, commits) — trusted
    DERIVED = "derived"  # machine-derived from operator content (summaries, analysis of own code)
    THIRD_PARTY = "third-party"  # ingested from outside the operator's control — untrusted default

    @classmethod
    def parse(cls, value: str) -> Trust:
        """Parse a config string to a :class:`Trust`, tolerant of ``_``/spaces for ``third-party``.

        Raises :class:`ValueError` on an unknown value so config validation surfaces it cleanly."""
        normalized = value.strip().lower().replace("_", "-").replace(" ", "-")
        try:
            return cls(normalized)
        except ValueError:
            allowed = ", ".join(level.value for level in cls)
            raise ValueError(f"unknown trust level {value!r}; allowed: {allowed}") from None

    @property
    def is_untrusted(self) -> bool:
        """Whether content at this level must be fenced on recall (anything but operator)."""
        return self is not Trust.OPERATOR
