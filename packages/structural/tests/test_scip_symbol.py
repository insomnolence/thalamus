"""Unit tests for the pure SCIP symbol-string parser.

The expected values are real strings emitted by ``scip-typescript`` 0.4.0 (captured in
Phase 0 from the ``ts_sample`` fixture), plus synthetic edge cases for escaping.
"""

from __future__ import annotations

import pytest
from thalamus.structural.scip_symbol import (
    Descriptor,
    ParsedSymbol,
    ScipSymbolError,
    Suffix,
    parse_symbol,
)


def _d(name: str, suffix: Suffix) -> Descriptor:
    return Descriptor(name, suffix)


def test_module_symbol() -> None:
    sym = parse_symbol("scip-typescript npm ts-sample 0.0.0 src/`shapes.ts`/")
    assert sym == ParsedSymbol(
        scheme="scip-typescript",
        manager="npm",
        name="ts-sample",
        version="0.0.0",
        descriptors=(_d("src", Suffix.NAMESPACE), _d("shapes.ts", Suffix.NAMESPACE)),
    )


def test_interface_and_method() -> None:
    sym = parse_symbol("scip-typescript npm ts-sample 0.0.0 src/`shapes.ts`/Shape#area().")
    assert sym.descriptors == (
        _d("src", Suffix.NAMESPACE),
        _d("shapes.ts", Suffix.NAMESPACE),
        _d("Shape", Suffix.TYPE),
        _d("area", Suffix.METHOD),
    )


def test_enum_member_is_term() -> None:
    sym = parse_symbol("scip-typescript npm ts-sample 0.0.0 src/`shapes.ts`/Kind#Round.")
    assert sym.descriptors[-2:] == (_d("Kind", Suffix.TYPE), _d("Round", Suffix.TERM))


def test_top_level_function() -> None:
    sym = parse_symbol("scip-typescript npm ts-sample 0.0.0 src/`geometry.ts`/circleArea().")
    assert sym.descriptors[-1] == _d("circleArea", Suffix.METHOD)


def test_method_then_parameter() -> None:
    sym = parse_symbol(
        "scip-typescript npm ts-sample 0.0.0 src/`geometry.ts`/circleArea().(radius)"
    )
    assert sym.descriptors[-2:] == (
        _d("circleArea", Suffix.METHOD),
        _d("radius", Suffix.PARAMETER),
    )


def test_backtick_escaped_constructor() -> None:
    sym = parse_symbol(
        "scip-typescript npm ts-sample 0.0.0 src/`circle.ts`/Circle#`<constructor>`()."
    )
    assert sym.descriptors[-2:] == (
        _d("Circle", Suffix.TYPE),
        _d("<constructor>", Suffix.METHOD),
    )


def test_external_lib_symbol() -> None:
    sym = parse_symbol("scip-typescript npm typescript 5.9.3 lib/`lib.es5.d.ts`/Math#PI.")
    assert sym.manager == "npm"
    assert sym.name == "typescript"
    assert sym.version == "5.9.3"
    assert sym.descriptors[-2:] == (_d("Math", Suffix.TYPE), _d("PI", Suffix.TERM))


def test_local_symbol() -> None:
    sym = parse_symbol("local 42")
    assert sym.is_local
    assert sym.local_id == "42"
    assert sym.descriptors == ()


def test_doubled_space_is_literal_in_package_name() -> None:
    # A package name containing a literal space escapes it by doubling.
    sym = parse_symbol("scip-typescript npm my  pkg 1.0.0 src/`a.ts`/")
    assert sym.name == "my pkg"
    assert sym.version == "1.0.0"


def test_doubled_backtick_is_literal() -> None:
    sym = parse_symbol("scip-typescript npm p 1.0.0 `a``b`#")
    assert sym.descriptors == (_d("a`b", Suffix.TYPE),)


def test_type_parameter() -> None:
    sym = parse_symbol("scip-typescript npm p 1.0.0 src/`a.ts`/Box#[T]")
    assert sym.descriptors[-1] == _d("T", Suffix.TYPE_PARAMETER)


def test_too_few_fields_raises() -> None:
    with pytest.raises(ScipSymbolError):
        parse_symbol("scip-typescript npm only-three")


def test_missing_suffix_raises() -> None:
    with pytest.raises(ScipSymbolError):
        parse_symbol("scip-typescript npm p 1.0.0 src/NoSuffix")
