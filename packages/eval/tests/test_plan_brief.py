from __future__ import annotations

from dataclasses import dataclass

import pytest
from thalamus.eval import (
    BriefEvalReport,
    GotchaCase,
    PlanBriefView,
    evaluate_plan_briefs,
    score_case,
    view_from_brief,
)


def _view(*ids: str, rendered: str = "") -> PlanBriefView:
    return PlanBriefView(surfaced_ids=frozenset(ids), rendered=rendered)


def test_case_requires_an_expectation() -> None:
    with pytest.raises(ValueError, match="expect_memory_id or expect_text"):
        GotchaCase(name="c", target="foo")


def test_memory_id_hit_and_miss() -> None:
    case = GotchaCase(name="c", target="foo", expect_memory_id="retained:abc")
    assert score_case(case, _view("retained:abc")).covered is True
    miss = score_case(case, _view("retained:other"))
    assert miss.covered is False
    assert "not surfaced" in miss.detail


def test_text_expectation_matches_rendered_brief() -> None:
    case = GotchaCase(name="c", target="foo", expect_text="must stay idempotent")
    assert score_case(case, _view(rendered="... bar must stay idempotent ...")).covered is True
    assert score_case(case, _view(rendered="nothing here")).covered is False


def test_memory_id_takes_precedence_but_text_is_a_fallback() -> None:
    # id absent but the fallback text is present → still covered.
    case = GotchaCase(name="c", target="foo", expect_memory_id="m1", expect_text="SQL injection")
    assert score_case(case, _view("m2", rendered="flags SQL injection here")).covered is True


def test_report_recall_and_misses() -> None:
    cases = [
        GotchaCase("hit", "a", expect_memory_id="m1"),
        GotchaCase("miss", "b", expect_memory_id="m2"),
    ]
    views = {"a": _view("m1"), "b": _view("zzz")}
    report = evaluate_plan_briefs(cases, lambda c: views[c.target])
    assert isinstance(report, BriefEvalReport)
    assert (report.total, report.covered) == (2, 1)
    assert report.recall == 0.5
    assert [r.name for r in report.misses] == ["miss"]


def test_empty_report_has_zero_recall() -> None:
    assert evaluate_plan_briefs([], lambda c: _view()).recall == 0.0


@dataclass
class _Item:
    memory_id: str


@dataclass
class _Finding:
    node_id: str


@dataclass
class _Brief:
    constraints: tuple[_Item, ...]
    context: tuple[_Item, ...]
    findings: tuple[_Finding, ...]

    def render(self) -> str:
        return "rendered brief body"


def test_view_from_brief_collects_memory_and_finding_ids() -> None:
    brief = _Brief(
        constraints=(_Item("m-gotcha"),),
        context=(_Item("m-decision"),),
        findings=(_Finding("finding:f1"),),
    )
    view = view_from_brief(brief)
    assert view.surfaced_ids == frozenset({"m-gotcha", "m-decision", "finding:f1"})
    assert view.rendered == "rendered brief body"
    # end-to-end: a case naming any surfaced id is covered through the real adapter.
    case = GotchaCase("c", "t", expect_memory_id="finding:f1")
    assert score_case(case, view).covered is True
