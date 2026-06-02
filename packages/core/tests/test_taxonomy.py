from __future__ import annotations

import pytest
from thalamus.core.exceptions import ThalamusError
from thalamus.core.taxonomy import (
    ACCEPTED_KINDS,
    RETAINED_KINDS,
    normalize_kind,
)


def test_canonical_kinds_pass_through_unchanged() -> None:
    for kind in RETAINED_KINDS:
        assert normalize_kind(kind) == kind


def test_claude_code_taxonomy_synonyms_map_to_canonical_kinds() -> None:
    # The native Claude Code memory types leak into remember calls; normalize, don't lose them.
    assert normalize_kind("project") == "decision"
    assert normalize_kind("user") == "preference"
    assert normalize_kind("feedback") == "preference"
    assert normalize_kind("reference") == "investigation"


def test_accepted_kinds_is_the_canonical_set_plus_synonyms() -> None:
    assert set(RETAINED_KINDS) <= set(ACCEPTED_KINDS)
    # Every accepted kind normalizes into the canonical set.
    assert {normalize_kind(kind) for kind in ACCEPTED_KINDS} <= set(RETAINED_KINDS)


def test_a_genuinely_unknown_kind_is_rejected_with_a_helpful_message() -> None:
    with pytest.raises(ThalamusError, match="unsupported retained memory kind"):
        normalize_kind("conversation")
