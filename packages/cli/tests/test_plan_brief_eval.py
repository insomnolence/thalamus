from __future__ import annotations

import json
from pathlib import Path

import pytest
from thalamus.cli.plan_brief_eval import load_brief_cases


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_brief_cases_parses_targets_and_expectations(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            {"name": "a", "target": "foo", "expect_memory_id": "retained:abc", "hops": 3},
            {"target": "bar", "expect_text": "must stay idempotent"},
        ],
    )
    cases = load_brief_cases(path)
    assert [c.name for c in cases] == ["a", "case-1"]  # name defaults to case-<index>
    assert cases[0].expect_memory_id == "retained:abc"
    assert cases[0].hops == 3
    assert cases[1].target == "bar" and cases[1].expect_text == "must stay idempotent"
    assert cases[1].hops is None


def test_load_brief_cases_rejects_a_case_without_a_target(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"name": "x", "expect_text": "y"}])
    with pytest.raises(ValueError, match="must be an object with a 'target'"):
        load_brief_cases(path)


def test_load_brief_cases_rejects_a_case_without_an_expectation(tmp_path: Path) -> None:
    # GotchaCase enforces this — a target with no expect_* is a no-op case.
    path = _write(tmp_path, [{"target": "foo"}])
    with pytest.raises(ValueError, match="expect_memory_id or expect_text"):
        load_brief_cases(path)


def test_load_brief_cases_requires_a_list(tmp_path: Path) -> None:
    path = _write(tmp_path, {"target": "foo"})
    with pytest.raises(ValueError, match="must be a JSON list"):
        load_brief_cases(path)


def test_shipped_example_cases_file_loads() -> None:
    example = Path(__file__).resolve().parents[3] / "docs" / "eval" / "plan_brief_cases.json"
    cases = load_brief_cases(example)
    assert len(cases) >= 1
    assert all(c.expect_memory_id or c.expect_text for c in cases)
