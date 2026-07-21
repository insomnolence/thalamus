"""Tests for the provenance/trust enum (§17.4 step 1)."""

from __future__ import annotations

import pytest
from thalamus.core.trust import Trust


def test_default_is_operator_and_trusted() -> None:
    assert Trust.OPERATOR.is_untrusted is False


def test_derived_and_third_party_are_untrusted() -> None:
    assert Trust.DERIVED.is_untrusted is True
    assert Trust.THIRD_PARTY.is_untrusted is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("operator", Trust.OPERATOR),
        ("third-party", Trust.THIRD_PARTY),
        ("third_party", Trust.THIRD_PARTY),
        ("Third Party", Trust.THIRD_PARTY),
        ("  DERIVED  ", Trust.DERIVED),
    ],
)
def test_parse_is_tolerant_of_separators_and_case(raw: str, expected: Trust) -> None:
    assert Trust.parse(raw) is expected


def test_parse_rejects_unknown_with_a_helpful_message() -> None:
    with pytest.raises(ValueError, match="unknown trust level"):
        Trust.parse("public")
