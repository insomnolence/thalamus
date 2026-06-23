"""The M-1a actuator-in-the-loop harness.

For each case it runs four arms; each arm = ``trials`` calls to the actuator with a
*uniform* prompt envelope whose only varying part is the arm's context block. Each
output is judged by the case's deterministic oracle. The harness records every
``(case, arm, trial, prompt, output, hit)`` so a run is fully auditable even though
individual LLM calls are not bit-reproducible.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from thalamus.eval.m1a.actuator import Actuator, ActuatorError
from thalamus.eval.m1a.cases import ARMS, Case

# The uniform envelope. Only ``{block}`` differs across arms; ``{task}`` is constant
# per case. Keep it terse and identical for every backend.
ENVELOPE = (
    "You are a coding actuator. Complete the task using the context provided.\n"
    "Be concise: output only the concrete change or decision you would make.\n\n"
    "## Task\n{task}\n\n## Context\n{block}\n"
)


@dataclass(frozen=True)
class TrialRecord:
    case_id: str
    arm: str
    trial: int
    temperature: float
    hit: bool
    output: str


@dataclass
class ArmResult:
    arm: str
    hits: int = 0
    trials: int = 0

    @property
    def avoid_rate(self) -> float:
        return self.hits / self.trials if self.trials else 0.0


@dataclass
class CaseResult:
    case_id: str
    case_set: str
    arms: dict[str, ArmResult] = field(default_factory=dict)

    def avoid_rate(self, arm: str) -> float:
        result = self.arms.get(arm)
        return result.avoid_rate if result else 0.0


def build_prompt(case: Case, arm: str) -> str:
    return ENVELOPE.format(task=case.task, block=case.block(arm))


def _act_resilient(
    actuator: Actuator, prompt: str, *, temperature: float, seed: int, retries: int
) -> str:
    """Call the actuator, retrying transient failures. A persistently-failing call returns an
    error marker (not a raise) so one bad call never aborts a long run — the marker fails the
    oracle, a *conservative* miss that can only bias against finding an effect, never inflate it."""
    last = ""
    for attempt in range(retries + 1):
        try:
            return actuator.act(prompt, temperature=temperature, seed=seed)
        except ActuatorError as exc:  # transient: timeout, connection reset, bad shape
            last = f"<actuator-error after {attempt + 1} attempt(s): {exc}>"
    return last


def run_case(
    case: Case,
    actuator: Actuator,
    *,
    trials: int = 8,
    temperatures: Sequence[float] = (0.2, 0.7),
    recorder: Callable[[TrialRecord], None] | None = None,
    retries: int = 2,
) -> CaseResult:
    """Run all four arms of one case. ``recorder`` (if given) receives every trial."""
    result = CaseResult(case_id=case.id, case_set=case.case_set)
    for arm in ARMS:
        prompt = build_prompt(case, arm)
        arm_result = ArmResult(arm=arm)
        for trial in range(trials):
            temperature = temperatures[trial % len(temperatures)]
            output = _act_resilient(
                actuator, prompt, temperature=temperature, seed=trial, retries=retries
            )
            hit = case.oracle.hit(output)
            arm_result.trials += 1
            arm_result.hits += int(hit)
            if recorder is not None:
                recorder(TrialRecord(case.id, arm, trial, temperature, hit, output))
        result.arms[arm] = arm_result
    return result


def run_cases(
    cases: Sequence[Case],
    actuator: Actuator,
    *,
    trials: int = 8,
    temperatures: Sequence[float] = (0.2, 0.7),
    recorder: Callable[[TrialRecord], None] | None = None,
    on_case: Callable[[CaseResult], None] | None = None,
    retries: int = 2,
) -> list[CaseResult]:
    """Run every case. ``on_case`` (if given) fires after each case for progress."""
    results: list[CaseResult] = []
    for case in cases:
        case_result = run_case(
            case, actuator, trials=trials, temperatures=temperatures,
            recorder=recorder, retries=retries,
        )
        results.append(case_result)
        if on_case is not None:
            on_case(case_result)
    return results
