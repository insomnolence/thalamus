"""M-1a — the gotcha-conversion probe (the actuator-in-the-loop harness).

A contained, opt-in eval that asks one falsifiable question: *conditional on the brain
holding the decisive memory, does surfacing it cause a fresh actuator to take an
objectively better action, above mere salience?* It is NOT the full thesis ablation —
see ``docs/eval/m1a_preregistration.md`` and ``docs/eval/m1a.md`` (usage).

Pieces: a pluggable :class:`Actuator` (Ollama / Claude / OpenAI·Codex / Gemini, plus a
test fixture), frozen :class:`Case` s with deterministic oracles, the :func:`run_cases`
harness, and the pre-committed :func:`summarize` statistics.
"""

from thalamus.eval.m1a.actuator import (
    Actuator,
    ActuatorError,
    AnthropicActuator,
    FixtureActuator,
    GeminiActuator,
    OllamaActuator,
    OpenAIActuator,
    build_actuator,
)
from thalamus.eval.m1a.cases import ARMS, CASE_SETS, Case, Oracle, cases_in_set, load_cases
from thalamus.eval.m1a.harness import (
    ArmResult,
    CaseResult,
    TrialRecord,
    build_prompt,
    run_case,
    run_cases,
)
from thalamus.eval.m1a.stats import Contrast, M1aReport, format_report, summarize

__all__ = [
    "ARMS",
    "CASE_SETS",
    "Actuator",
    "ActuatorError",
    "AnthropicActuator",
    "ArmResult",
    "Case",
    "CaseResult",
    "Contrast",
    "FixtureActuator",
    "GeminiActuator",
    "M1aReport",
    "OllamaActuator",
    "OpenAIActuator",
    "Oracle",
    "TrialRecord",
    "build_actuator",
    "build_prompt",
    "cases_in_set",
    "format_report",
    "load_cases",
    "run_case",
    "run_cases",
    "summarize",
]
