from __future__ import annotations

import json
from pathlib import Path

import pytest
from thalamus.eval.m1a import (
    ARMS,
    ActuatorError,
    Case,
    FixtureActuator,
    Oracle,
    load_cases,
    run_case,
    run_cases,
    summarize,
)
from thalamus.eval.m1a.cases import parse_case
from thalamus.eval.m1a.stats import bootstrap_ci, sign_test_p


# A fixture actuator that "takes the gotcha-avoiding action" iff the decisive memory
# (marked DECISIVE) is present in the prompt — simulating the brain's delivery effect.
def _fixture() -> FixtureActuator:
    def responder(prompt: str) -> str:
        if "DECISIVE" in prompt:
            return "I will rebuild the index first, then change the encoder."
        return "I'll just change the encoder."

    return FixtureActuator(responder)


def _positive(case_id: str) -> Case:
    return Case(
        id=case_id,
        task="change the encoder",
        arms={
            "off": "(no context)",
            "salience": "watch for known gotchas",
            "content_ablation": "a brief about encoders",
            "full": "a brief about encoders. DECISIVE: rebuilding compatible indexes is required.",
        },
        oracle=Oracle(required=("rebuild the index",)),
        case_set="positive",
    )


def _control(case_id: str) -> Case:
    return Case(
        id=case_id,
        task="an unrelated task",
        arms={arm: "a brief with general context" for arm in ARMS},
        oracle=Oracle(required=("rebuild the index",)),
        case_set="negative_control",
    )


# ---- oracle / cases ----

def test_oracle_requires_and_forbids() -> None:
    oracle = Oracle(required=("parametri",), forbidden=(r"eval\(",))
    assert oracle.hit("use a parametrized query")
    assert not oracle.hit("use eval( to parametrize")
    assert not oracle.hit("just a plain string")


def test_parse_case_requires_all_arms() -> None:
    with pytest.raises(ValueError, match="missing arm"):
        parse_case({"id": "c", "task": "t", "arms": {"off": "", "full": ""},
                    "oracle": {"forbidden": ["x"]}})


def test_parse_case_requires_oracle_patterns() -> None:
    with pytest.raises(ValueError, match="no required/forbidden"):
        parse_case({"id": "c", "task": "t", "arms": {a: "" for a in ARMS}, "oracle": {}})


def test_load_cases_roundtrip(tmp_path: Path) -> None:
    data = [{"id": "c1", "task": "t", "arms": {a: "" for a in ARMS},
             "oracle": {"forbidden": [r"eval\("]}, "set": "positive"}]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    cases = load_cases(path)
    assert len(cases) == 1 and cases[0].id == "c1"


# ---- harness ----

def test_full_arm_hits_when_memory_present() -> None:
    result = run_case(_positive("p1"), _fixture(), trials=4)
    assert result.avoid_rate("full") == 1.0
    assert result.avoid_rate("content_ablation") == 0.0


def test_run_case_survives_a_failing_actuator() -> None:
    def boom(_prompt: str) -> str:
        raise ActuatorError("simulated timeout")

    # A persistently-failing actuator must not crash the run; every trial is a conservative miss.
    result = run_case(_positive("p1"), FixtureActuator(boom), trials=3, retries=1)
    assert result.arms["full"].trials == 3
    assert result.avoid_rate("full") == 0.0


# ---- stats / criteria ----

def test_positive_set_clears_control_and_passes() -> None:
    cases = [_positive("p1"), _positive("p2"), _positive("p3"), _control("c1"), _control("c2")]
    report = summarize(run_cases(cases, _fixture(), trials=4), seed=1)
    assert report.primary.mean_delta == 1.0
    assert report.negative_control_delta == 0.0
    assert report.criteria["clears_negative_control"]
    assert report.passes


def test_no_control_set_fails_the_gate() -> None:
    report = summarize(run_cases([_positive("p1")], _fixture(), trials=2))
    assert not report.criteria["has_negative_control"]
    assert not report.passes


def test_sign_test_and_bootstrap() -> None:
    assert sign_test_p([0.2, 0.3, 0.5, 0.1]) == pytest.approx(0.125)
    assert sign_test_p([0.0, 0.0]) == 1.0
    lo, hi = bootstrap_ci([0.5, 0.5, 0.5], seed=0)
    assert lo == 0.5 and hi == 0.5
