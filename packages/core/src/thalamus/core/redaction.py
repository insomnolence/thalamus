"""Deterministic secret redaction at the ingest boundary (§17.4 step 3 — threat T2).

A brain embeds free text — commit subjects, retained notes, docs, arbitrary ``text`` corpora — and
any of it can sweep in an API key, token, password, or private key. Once embedded a secret lives in
the vector index *and* Neo4j, is surfaced again on every relevant recall, and is far harder to
expunge than a single file on disk (you cannot ``git rm`` an embedding). So we scrub at the
**capture boundary**, before anything is embedded or stored — the cheap, irreversible-if-skipped
moment (§17.4: "redaction-after-the-fact on an embedded store is the hard case we want to avoid").

Deterministic by design (§4 — deterministic analysis over latent geometry): known key/token shapes
plus a bounded scanner for secret-named ``KEY=value`` assignments. No model and no entropy
guesswork in the default path — a generic high-entropy sweep is available
(``include_high_entropy=True``) but
**off by default** because it false-positives on the hashes, ids, and base64 blobs that legitimately
fill a code-aware brain. Each match becomes ``[REDACTED:<kind>]``; what was found is recorded only
as a :class:`RedactionEvent` (kind + count, **never the secret text**) so coverage is auditable
without re-leaking the very thing we removed.

Pure and dependency-free (this is ``core``): callers at the two write boundaries
(``cli.remember``, ``experiential.episode``, the ``docs``/``text`` ingestors) apply it before embed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RedactionEvent:
    """An auditable record that a secret of ``kind`` was redacted ``count`` time(s).

    Deliberately carries **no** captured text — logging the secret to prove we redacted it would
    re-introduce the leak we just closed. Kind + count is enough to audit coverage."""

    kind: str
    count: int


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """The scrubbed text plus the events describing what was removed (empty if nothing matched)."""

    text: str
    events: tuple[RedactionEvent, ...]

    @property
    def redacted(self) -> bool:
        """Whether any secret was removed."""
        return bool(self.events)


def merge_redaction_events(events: Iterable[RedactionEvent]) -> tuple[RedactionEvent, ...]:
    """Sum events by kind into one event per kind (sorted) — for aggregating across several
    :func:`redact_secrets` calls (e.g. a memory's text + why) into a single audit record."""
    counts: dict[str, int] = {}
    for event in events:
        counts[event.kind] = counts.get(event.kind, 0) + event.count
    return tuple(RedactionEvent(kind, counts[kind]) for kind in sorted(counts))


@dataclass(frozen=True, slots=True)
class _Pattern:
    """One distinctive, bounded secret shape."""

    kind: str
    regex: re.Pattern[str]


# Specific, distinctive shapes. Secret-named assignments are handled separately by the bounded
# scanner below: expressing optional quotes, delimiters, function calls, and prose exceptions as
# one regex made both its runtime and its boundary behavior too hard to reason about.
_PATTERNS: tuple[_Pattern, ...] = (
    # AWS access-key ids (AKIA/ASIA/AGPA/AIDA + 16 upper-alnum).
    _Pattern("aws-access-key", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA)[0-9A-Z]{16}\b")),
    # GitHub PATs / OAuth / app tokens (ghp_/gho_/ghu_/ghs_/ghr_ + 36+; fine-grained github_pat_).
    _Pattern(
        "github-token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    ),
    # Slack tokens (xoxb-/xoxp-/xoxa-/xoxr-/xoxs-).
    _Pattern("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    # Google API keys (AIza + 35).
    _Pattern("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    # OpenAI / Anthropic style secret keys (sk- / sk-ant- + a long body).
    _Pattern("api-key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{20,}\b")),
    # JWTs (three base64url segments, header starts ``eyJ``).
    _Pattern("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")),
)

# Assignment heads and URL separators are only located with bounded/literal searches. Ambiguous
# values are scanned once, character by character, so long benign input cannot trigger regex
# backtracking.
_ASSIGNMENT_HEAD = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])"
    r"(?P<key>['\"]?[A-Za-z_][A-Za-z0-9_-]{0,63}['\"]?)"
    r"(?P<before>[ \t]{0,32})(?P<separator>[:=])(?P<after>[ \t\r\n]{0,32})"
)
_SECRET_KEY_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "accesskey",
    "private_key",
    "privatekey",
    "credential",
)
_AUTH_FIELD_NAMES = frozenset({"authorization", "auth_key", "auth_header", "auth_data"})
_AUTH_FIELD_SUFFIXES = frozenset({"auth", "authorization"})
_UNQUOTED_VALUE_DELIMITERS = frozenset(" \t\r\n'\";,()[]{}")
_URL_COMPONENT_DELIMITERS = frozenset("/ \t\r\n:@")
_URL_SCHEME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+.-"
)
_MAX_URL_SCHEME_CHARS = 64
_PEM_BEGIN = "-----BEGIN "
_PEM_END = "-----END "
_PEM_PRIVATE_KEY_SUFFIX = "PRIVATE KEY-----"
_MAX_PEM_LABEL_CHARS = 64


def _apply_edits(text: str, edits: list[tuple[int, int]], placeholder: str) -> str:
    """Apply ordered, non-overlapping spans without rebuilding ``text`` for every edit."""
    if not edits:
        return text
    parts: list[str] = []
    cursor = 0
    for start, end in edits:
        parts.extend((text[cursor:start], placeholder))
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _is_secret_assignment_key(key: str) -> bool:
    """Return whether ``key`` explicitly names credential material.

    Bare ``auth`` is intentionally not a marker: prose and analyzer output commonly contain
    harmless pairs such as ``auth=authentication``. Compound names such as ``auth_token`` still
    match the stronger ``token`` marker.
    """
    normalized = key.strip("'\"").lower().replace("-", "_")
    return any(marker in normalized for marker in _SECRET_KEY_MARKERS)


def _is_auth_assignment_key(key: str) -> bool:
    """Recognize auth headers/fields by their final key component.

    This is deliberately structural, not an allowlist of HTTP schemes or a guess about whether a
    value "looks secret". It covers Authorization, Proxy-Authorization, X-Authorization,
    HTTP_AUTHORIZATION, session_auth, plus the explicit legacy forms auth_key/auth_header/auth_data.
    Bare ``auth`` stays excluded because analyzer prose commonly uses ``auth=authentication``.
    """
    normalized = key.strip("'\"").lower().replace("-", "_")
    components = normalized.split("_")
    return normalized in _AUTH_FIELD_NAMES or (
        len(components) > 1 and components[-1] in _AUTH_FIELD_SUFFIXES
    )


def _pem_marker_end(text: str, start: int, marker: str) -> int | None:
    """Return the end of a bounded ``BEGIN/END … PRIVATE KEY`` marker."""
    label_start = start + len(marker)
    search_end = min(
        len(text),
        label_start + _MAX_PEM_LABEL_CHARS + len(_PEM_PRIVATE_KEY_SUFFIX),
    )
    suffix_start = text.find(_PEM_PRIVATE_KEY_SUFFIX, label_start, search_end)
    if suffix_start < 0:
        return None
    label = text[label_start:suffix_start]
    if label and (
        not label.endswith(" ")
        or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 " for char in label)
    ):
        return None
    return suffix_start + len(_PEM_PRIVATE_KEY_SUFFIX)


def _redact_private_keys(text: str) -> tuple[str, int]:
    """Redact PEM private-key blocks with a monotonic, linear scanner.

    Once a valid begin marker is found, the next valid private-key end marker closes it, matching
    the former non-greedy regex semantics. An unmatched begin scans the remaining input once and
    terminates, rather than restarting a suffix search at every later begin marker.
    """
    edits: list[tuple[int, int]] = []
    search_from = 0
    while (begin := text.find(_PEM_BEGIN, search_from)) >= 0:
        begin_end = _pem_marker_end(text, begin, _PEM_BEGIN)
        if begin_end is None:
            search_from = begin + len(_PEM_BEGIN)
            continue

        end_search = begin_end
        while (end := text.find(_PEM_END, end_search)) >= 0:
            end_end = _pem_marker_end(text, end, _PEM_END)
            if end_end is not None:
                edits.append((begin, end_end))
                search_from = end_end
                break
            end_search = end + len(_PEM_END)
        else:
            # No valid end marker remains. Later begin markers cannot form a complete block either.
            break

    return _apply_edits(text, edits, "[REDACTED:private-key]"), len(edits)


def _assignment_value_span(text: str, start: int) -> tuple[int, int] | None:
    """Find one assignment value without regex backtracking.

    Returns the value's half-open span, excluding surrounding quotes and delimiters. Function calls
    are deliberately skipped: ``token=get_token()`` names a producer, not a captured credential.
    """
    if start >= len(text):
        return None
    quote = text[start] if text[start] in {"'", '"'} else ""
    value_start = start + 1 if quote else start
    if text.startswith("[REDACTED:", value_start):
        return None

    cursor = value_start
    escaped = False
    while cursor < len(text):
        char = text[cursor]
        if quote:
            if char == quote and not escaped:
                break
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
        elif char in _UNQUOTED_VALUE_DELIMITERS:
            break
        cursor += 1

    value = text[value_start:cursor]
    if len(value) < 8:
        return None
    if not quote and cursor < len(text) and text[cursor] == "(":
        return None
    return value_start, cursor


def _auth_assignment_value_span(
    text: str, start: int, *, wrapper_quote: str = ""
) -> tuple[int, int] | None:
    """Find the secret portion of an auth field without enumerating schemes.

    Policy is intentionally fail-closed:

    * ``<scheme> <credential>`` preserves the first word and redacts everything after it;
    * a single opaque value is redacted in full, regardless of length or character shape.

    Quoted mapping/header values stop at their closing quote. Unquoted values stop at the line
    boundary. This accepts benign text loss inside a recognized auth field to close the credential
    class rather than trading leaks against an ever-growing scheme allowlist.
    """
    if start >= len(text):
        return None
    value_quote = text[start] if text[start] in {"'", '"'} else ""
    terminator_quote = value_quote or wrapper_quote
    value_start = start + 1 if value_quote else start
    cursor = value_start
    escaped = False
    while cursor < len(text):
        char = text[cursor]
        if terminator_quote:
            if char == terminator_quote and not escaped:
                break
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
        elif char in "\r\n":
            break
        cursor += 1

    value_end = cursor
    while value_end > value_start and text[value_end - 1] in " \t":
        value_end -= 1
    folded = (
        _folded_continuation_span(text, cursor)
        if not terminator_quote
        else None
    )
    folded_is_redacted = (
        folded is not None and text.startswith("[REDACTED:", folded[0])
    )
    if value_start == value_end:
        return None if folded_is_redacted else folded
    if text.startswith("[REDACTED:", value_start):
        return None if folded_is_redacted else folded

    separator = value_start
    while separator < value_end and text[separator] not in " \t":
        separator += 1
    credential_start = separator
    while credential_start < value_end and text[credential_start] in " \t":
        credential_start += 1
    if credential_start < value_end:
        if text.startswith("[REDACTED:", credential_start):
            return None if folded_is_redacted else folded
        return (
            credential_start,
            folded[1] if folded is not None and not folded_is_redacted else value_end,
        )
    if folded is not None:
        # A single first-line word is the scheme when an obsolete folded continuation follows.
        return None if folded_is_redacted else folded
    return value_start, value_end


def _folded_continuation_span(text: str, start: int) -> tuple[int, int] | None:
    """Return the content span of consecutive RFC 7230 obs-fold continuation lines."""
    cursor = start
    content_start: int | None = None
    content_end: int | None = None
    while cursor < len(text):
        if text.startswith("\r\n", cursor):
            line_start = cursor + 2
        elif text[cursor] in "\r\n":
            line_start = cursor + 1
        else:
            break
        if line_start >= len(text) or text[line_start] not in " \t":
            break
        while line_start < len(text) and text[line_start] in " \t":
            line_start += 1
        line_end = line_start
        while line_end < len(text) and text[line_end] not in "\r\n":
            line_end += 1
        trimmed_end = line_end
        while trimmed_end > line_start and text[trimmed_end - 1] in " \t":
            trimmed_end -= 1
        if trimmed_end > line_start:
            if content_start is None:
                content_start = line_start
            content_end = trimmed_end
        cursor = line_end
    if content_start is None or content_end is None:
        return None
    return content_start, content_end


def _redact_assignments(text: str) -> tuple[str, int]:
    """Redact secret assignment values with a linear scan and return text + match count."""
    edits: list[tuple[int, int]] = []
    search_from = 0
    while (match := _ASSIGNMENT_HEAD.search(text, search_from)) is not None:
        key = match.group("key")
        if _is_secret_assignment_key(key):
            span = _assignment_value_span(text, match.end())
        elif _is_auth_assignment_key(key):
            wrapper_quote = (
                key[0]
                if key[0] in {"'", '"'} and not key.endswith(key[0])
                else ""
            )
            span = _auth_assignment_value_span(
                text, match.end(), wrapper_quote=wrapper_quote
            )
        else:
            search_from = match.end()
            continue
        if span is not None:
            edits.append(span)
            # The value itself may contain another assignment-shaped substring. Redacting the
            # outer credential covers it; skipping past the span prevents overlapping stale edits.
            search_from = span[1]
        else:
            search_from = match.end()
    return _apply_edits(text, edits, "[REDACTED:env-assignment]"), len(edits)


def _scheme_start(text: str, separator: int) -> int | None:
    """Return the start of a practical URI scheme immediately before ``://``."""
    start = separator
    scanned = 0
    while start > 0 and text[start - 1] in _URL_SCHEME_CHARS:
        start -= 1
        scanned += 1
        if scanned > _MAX_URL_SCHEME_CHARS:
            return None
    if start == separator or not text[start].isalpha() or not text[start].isascii():
        return None
    return start


def _redact_basic_auth_urls(text: str) -> tuple[str, int]:
    """Redact ``scheme://user:password@host`` passwords with a bounded linear scanner."""
    edits: list[tuple[int, int]] = []
    search_from = 0
    while (separator := text.find("://", search_from)) >= 0:
        if _scheme_start(text, separator) is None:
            search_from = separator + 3
            continue

        username_start = separator + 3
        cursor = username_start
        while cursor < len(text) and text[cursor] not in _URL_COMPONENT_DELIMITERS:
            cursor += 1
        if cursor == username_start or cursor >= len(text) or text[cursor] != ":":
            search_from = separator + 3
            continue

        password_start = cursor + 1
        if text.startswith("[REDACTED:", password_start):
            search_from = password_start + len("[REDACTED:")
            continue
        cursor = password_start
        while cursor < len(text) and text[cursor] not in _URL_COMPONENT_DELIMITERS:
            cursor += 1
        if cursor < len(text) and text[cursor] == "@" and cursor > password_start:
            edits.append((password_start, cursor))
            search_from = cursor + 1
        else:
            search_from = separator + 3

    return _apply_edits(text, edits, "[REDACTED:basic-auth-url]"), len(edits)

_HIGH_ENTROPY_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/"
)


def _redact_high_entropy(text: str) -> tuple[str, int]:
    """Redact mixed-case base64-ish runs in one pass.

    This remains opt-in because false positives are expected. The scanner replaces the prior
    lookahead regex, whose repeated word-boundary starts could rescan a long candidate suffix.
    """
    edits: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(text):
        if text[cursor] not in _HIGH_ENTROPY_CHARS:
            cursor += 1
            continue
        run_start = cursor
        while cursor < len(text) and text[cursor] in _HIGH_ENTROPY_CHARS:
            cursor += 1
        run_end = cursor

        candidate_start = run_start
        while candidate_start < run_end and text[candidate_start] in "+/":
            candidate_start += 1
        candidate_end = run_end
        while candidate_end > candidate_start and text[candidate_end - 1] in "+/":
            candidate_end -= 1
        candidate = text[candidate_start:candidate_end]
        if (
            len(candidate) >= 32
            and any(char.islower() for char in candidate)
            and any(char.isupper() for char in candidate)
            and any(char.isdigit() for char in candidate)
        ):
            padded_end = run_end
            while padded_end < len(text) and text[padded_end] == "=" and padded_end - run_end < 2:
                padded_end += 1
            edits.append((candidate_start, padded_end))
            cursor = padded_end

    return _apply_edits(text, edits, "[REDACTED:high-entropy]"), len(edits)


def redact_secrets(text: str, *, include_high_entropy: bool = False) -> RedactionResult:
    """Scrub credential-shaped substrings from ``text``, returning cleaned text + an audit trail.

    Each pattern is applied in turn; matches become ``[REDACTED:<kind>]`` (the key/structure of a
    ``KEY=value`` or URL credential is kept, only the secret value removed).
    ``include_high_entropy`` adds a generic base64-blob sweep — off by default (false-positives on
    code hashes/ids). The result carries kind+count events only, never the captured secret."""
    if not text:
        return RedactionResult(text, ())
    counts: dict[str, int] = {}
    result, private_key_count = _redact_private_keys(text)
    if private_key_count:
        counts["private-key"] = private_key_count
    for pat in _PATTERNS:

        def _sub(match: re.Match[str], kind: str = pat.kind) -> str:
            del match
            counts[kind] = counts.get(kind, 0) + 1
            return f"[REDACTED:{kind}]"

        result = pat.regex.sub(_sub, result)
    result, basic_auth_count = _redact_basic_auth_urls(result)
    if basic_auth_count:
        counts["basic-auth-url"] = basic_auth_count
    result, assignment_count = _redact_assignments(result)
    if assignment_count:
        counts["env-assignment"] = assignment_count
    if include_high_entropy:
        result, high_entropy_count = _redact_high_entropy(result)
        if high_entropy_count:
            counts["high-entropy"] = high_entropy_count
    events = tuple(RedactionEvent(kind, counts[kind]) for kind in sorted(counts))
    return RedactionResult(result, events)
