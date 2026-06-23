"""M-1a cases and their deterministic oracles.

A case is frozen *before* any run (the pre-registration hard gate, see
``docs/eval/m1a_preregistration.md``): it carries the task cue, the four arm context
blocks, and a deterministic oracle that decides — without any model — whether an
actuator's output took the gotcha-avoiding action. Cases live in a JSON file so a run
is fully reproducible from a committed artifact.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The four arms differ ONLY in the context block (the prompt envelope is uniform):
#   off              — length-matched placebo (no memory)
#   salience         — off + a generic content-free nudge
#   content_ablation — the full brief minus the one relevant memory
#   full             — the full brief (the relevant memory present)
ARMS: tuple[str, ...] = ("off", "salience", "content_ablation", "full")

# Case sets (pre-registration §"Case sets").
CASE_SETS: tuple[str, ...] = ("positive", "negative_control", "adversarial")


@dataclass(frozen=True)
class Oracle:
    """A deterministic verdict on one actuator output — code, never a model.

    The output counts as the gotcha-avoiding action iff every ``required`` regex
    matches AND no ``forbidden`` regex matches (case-insensitive). For the
    prohibitive class (a constraint that manifests as an *absence*), ``forbidden``
    carries the mechanical signature of the mistake.
    """

    required: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()

    def hit(self, output: str) -> bool:
        if any(re.search(pattern, output, re.IGNORECASE) is None for pattern in self.required):
            return False
        return all(
            re.search(pattern, output, re.IGNORECASE) is None for pattern in self.forbidden
        )


@dataclass(frozen=True)
class Case:
    """One frozen M-1a case."""

    id: str
    task: str
    arms: dict[str, str]
    oracle: Oracle
    case_set: str = "positive"
    memory_id: str | None = None

    def block(self, arm: str) -> str:
        return self.arms.get(arm, "")


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("oracle 'required'/'forbidden' must be a list of strings")
    return tuple(str(item) for item in value)


def parse_case(raw: dict[str, Any]) -> Case:
    try:
        case_id = str(raw["id"])
        task = str(raw["task"])
        arms_raw = raw["arms"]
    except KeyError as exc:
        raise ValueError(f"case missing required field: {exc}") from exc
    if not isinstance(arms_raw, dict):
        raise ValueError(f"case {case_id!r}: 'arms' must be an object")
    missing = [arm for arm in ARMS if arm not in arms_raw]
    if missing:
        raise ValueError(f"case {case_id!r}: missing arm block(s): {', '.join(missing)}")
    arms = {arm: str(arms_raw[arm]) for arm in ARMS}
    oracle_raw = raw.get("oracle", {})
    if not isinstance(oracle_raw, dict):
        raise ValueError(f"case {case_id!r}: 'oracle' must be an object")
    oracle = Oracle(
        required=_as_str_tuple(oracle_raw.get("required")),
        forbidden=_as_str_tuple(oracle_raw.get("forbidden")),
    )
    if not oracle.required and not oracle.forbidden:
        raise ValueError(f"case {case_id!r}: oracle has no required/forbidden patterns")
    case_set = str(raw.get("set", "positive"))
    if case_set not in CASE_SETS:
        raise ValueError(f"case {case_id!r}: unknown set {case_set!r} (one of {CASE_SETS})")
    memory_id = raw.get("memory_id")
    return Case(
        id=case_id,
        task=task,
        arms=arms,
        oracle=oracle,
        case_set=case_set,
        memory_id=None if memory_id is None else str(memory_id),
    )


def load_cases(path: str | Path) -> list[Case]:
    """Load and validate a frozen cases file (a JSON list of case objects)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("cases file must be a JSON list of case objects")
    cases = [parse_case(item) for item in raw]
    ids = [case.id for case in cases]
    duplicates = {cid for cid in ids if ids.count(cid) > 1}
    if duplicates:
        raise ValueError(f"duplicate case id(s): {', '.join(sorted(duplicates))}")
    return cases


def cases_in_set(cases: Sequence[Case], case_set: str) -> list[Case]:
    return [case for case in cases if case.case_set == case_set]
