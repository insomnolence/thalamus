"""Parse SCIP symbol strings into their structured form.

SCIP encodes every symbol as a string with a defined grammar (``scip.proto`` + the
symbol-format spec)::

    <symbol>  ::= <scheme> ' ' <package> ' ' (<descriptor>)+   |   'local ' <local-id>
    <package> ::= <manager> ' ' <name> ' ' <version>

Spaces inside the scheme/package fields are escaped by **doubling**; descriptors carry
no unescaped spaces (names with special characters are **backtick-escaped**). Each
descriptor ends in a suffix sentinel: ``/`` namespace, ``#`` type, ``.`` term,
``().`` method, ``(x)`` parameter, ``[x]`` type-parameter, ``:`` meta, ``!`` macro.

This module is **pure** — it operates on the string, never on protobuf — so it is fast
to unit-test against the real strings ``scip-typescript`` emits. The ingestor
(:mod:`thalamus.structural.scip_ingestor`) uses it to derive node ids and kinds.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Characters that terminate a bare (non-backtick) descriptor name — the suffix
# sentinels plus the closing brackets of parameter / type-parameter descriptors.
_SENTINELS = "/#.([:!)]"


class Suffix(Enum):
    """A SCIP descriptor's kind, from its trailing sentinel."""

    NAMESPACE = "namespace"  # ``name/``  — packages, modules, files
    TYPE = "type"  # ``name#``  — class / interface / enum
    TERM = "term"  # ``name.``  — variable / property / enum member
    METHOD = "method"  # ``name().`` — function / method / constructor
    TYPE_PARAMETER = "type_parameter"  # ``[name]``
    PARAMETER = "parameter"  # ``(name)``
    META = "meta"  # ``name:``
    MACRO = "macro"  # ``name!``


@dataclass(frozen=True, slots=True)
class Descriptor:
    name: str
    suffix: Suffix


@dataclass(frozen=True, slots=True)
class ParsedSymbol:
    """A SCIP symbol string broken into scheme + package + descriptor chain.

    ``is_local`` symbols (``local <id>``) carry only ``local_id`` — they are
    function-local and never become structural nodes.
    """

    scheme: str
    manager: str
    name: str
    version: str
    descriptors: tuple[Descriptor, ...]
    is_local: bool = False
    local_id: str = ""


class ScipSymbolError(ValueError):
    """A malformed SCIP symbol string."""


def parse_symbol(symbol: str) -> ParsedSymbol:
    """Parse a SCIP symbol string into a :class:`ParsedSymbol`."""
    if symbol.startswith("local "):
        local_id = symbol[len("local ") :]
        return ParsedSymbol("local", "", "", "", (), is_local=True, local_id=local_id)
    fields, rest = _take_space_escaped(symbol, 4)
    if len(fields) < 4:
        raise ScipSymbolError(f"symbol has too few space-escaped fields: {symbol!r}")
    scheme, manager, name, version = fields
    return ParsedSymbol(scheme, manager, name, version, _parse_descriptors(rest))


def _take_space_escaped(s: str, count: int) -> tuple[list[str], str]:
    """Split off ``count`` space-delimited fields (doubled space = literal space).

    Returns the fields and the unparsed remainder (the descriptor string), which keeps
    its own (backtick) escaping and is parsed separately.
    """
    fields: list[str] = []
    buf: list[str] = []
    i, n = 0, len(s)
    while i < n and len(fields) < count:
        c = s[i]
        if c == " ":
            if i + 1 < n and s[i + 1] == " ":  # escaped literal space
                buf.append(" ")
                i += 2
                continue
            fields.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    return fields, s[i:]


def _parse_descriptors(s: str) -> tuple[Descriptor, ...]:
    descriptors: list[Descriptor] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "(":  # Parameter: ``(name)``
            name, i = _read_name(s, i + 1)
            i = _expect(s, i, ")")
            descriptors.append(Descriptor(name, Suffix.PARAMETER))
            continue
        if c == "[":  # TypeParameter: ``[name]``
            name, i = _read_name(s, i + 1)
            i = _expect(s, i, "]")
            descriptors.append(Descriptor(name, Suffix.TYPE_PARAMETER))
            continue
        name, i = _read_name(s, i)
        if i >= n:
            raise ScipSymbolError(f"descriptor missing suffix sentinel: {s!r}")
        sentinel = s[i]
        if sentinel == "/":
            descriptors.append(Descriptor(name, Suffix.NAMESPACE))
            i += 1
        elif sentinel == "#":
            descriptors.append(Descriptor(name, Suffix.TYPE))
            i += 1
        elif sentinel == ":":
            descriptors.append(Descriptor(name, Suffix.META))
            i += 1
        elif sentinel == "!":
            descriptors.append(Descriptor(name, Suffix.MACRO))
            i += 1
        elif sentinel == "(":  # Method: ``name(<disambiguator>).``
            i += 1
            while i < n and s[i] != ")":  # skip the disambiguator
                i += 1
            i = _expect(s, i, ")")
            i = _expect(s, i, ".")
            descriptors.append(Descriptor(name, Suffix.METHOD))
        elif sentinel == ".":  # Term
            descriptors.append(Descriptor(name, Suffix.TERM))
            i += 1
        else:
            raise ScipSymbolError(f"unknown descriptor sentinel {sentinel!r} in {s!r}")
    return tuple(descriptors)


def _read_name(s: str, i: int) -> tuple[str, int]:
    """Read a descriptor name — backtick-escaped (``\\`` doubles) or bare."""
    n = len(s)
    if i < n and s[i] == "`":
        i += 1
        buf: list[str] = []
        while i < n:
            if s[i] == "`":
                if i + 1 < n and s[i + 1] == "`":  # escaped literal backtick
                    buf.append("`")
                    i += 2
                    continue
                return "".join(buf), i + 1  # closing backtick
            buf.append(s[i])
            i += 1
        raise ScipSymbolError(f"unterminated backtick name in {s!r}")
    start = i
    while i < n and s[i] not in _SENTINELS:
        i += 1
    return s[start:i], i


def _expect(s: str, i: int, char: str) -> int:
    if i >= len(s) or s[i] != char:
        raise ScipSymbolError(f"expected {char!r} at index {i} in {s!r}")
    return i + 1
