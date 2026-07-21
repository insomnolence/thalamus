"""Deterministic secret redaction at the ingest boundary (§17.4 step 3 — threat T2).

A brain embeds free text — commit subjects, retained notes, docs, arbitrary ``text`` corpora — and
any of it can sweep in an API key, token, password, or private key. Once embedded a secret lives in
the vector index *and* Neo4j, is surfaced again on every relevant recall, and is far harder to
expunge than a single file on disk (you cannot ``git rm`` an embedding). So we scrub at the
**capture boundary**, before anything is embedded or stored — the cheap, irreversible-if-skipped
moment (§17.4: "redaction-after-the-fact on an embedded store is the hard case we want to avoid").

Deterministic by design (§4 — deterministic analysis over latent geometry): known key/token shapes
and secret-named ``KEY=value`` assignments, matched by regex. No model and no entropy guesswork in
the default path — a generic high-entropy sweep is available (``include_high_entropy=True``) but
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
    """One secret shape. ``keep_prefix_group`` (0 = none) names a capture group whose text is kept
    in front of the placeholder — used to redact only the *value* of a ``KEY=value`` assignment or
    only the password in a ``scheme://user:pass@host`` URL, leaving the harmless key/structure."""

    kind: str
    regex: re.Pattern[str]
    keep_prefix_group: int = 0


# Specific, distinctive shapes first; the generic ``KEY=value`` assignment last (a negative
# lookahead keeps it from re-matching a placeholder a prior pattern already wrote). All are
# deterministic — no provider lookups, no network. Bounds are deliberately loose enough to catch
# real credentials and tight enough (anchored prefixes / word boundaries) to avoid mangling prose.
_PATTERNS: tuple[_Pattern, ...] = (
    # PEM private-key blocks (RSA/EC/OPENSSH/PGP/…), whole block including the body.
    _Pattern(
        "private-key",
        re.compile(
            r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?"
            r"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
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
    # Credentials embedded in a URL: ``scheme://user:PASSWORD@host`` — redact only the password.
    _Pattern(
        "basic-auth-url",
        re.compile(
            r"([a-z][a-z0-9+.\-]*://[^\s:/@]+:)(?!\[REDACTED:)([^\s:/@]+)(?=@)", re.IGNORECASE
        ),
        keep_prefix_group=1,
    ),
    # Secret-named assignment: ``…PASSWORD/SECRET/TOKEN/API_KEY/… = / : value`` — redact the value.
    # The value (≥8 chars, no whitespace/quotes/parens) must also contain a digit or a credential
    # symbol (``+/=.-``); a bare identifier like ``token = get_token()`` is left alone, since real
    # keys/passwords carry digits or symbols while function names and prose words do not.
    _Pattern(
        "env-assignment",
        re.compile(
            r"(?i)([A-Za-z0-9_]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|"
            r"private[_-]?key|credential|auth)[A-Za-z0-9_]*\s*[:=]\s*['\"]?)"
            r"(?!\[REDACTED:)(?=[^\s'\"()]*[0-9+/=.\-])([^\s'\"()]{8,})"
        ),
        keep_prefix_group=1,
    ),
)

# Generic high-entropy base64-ish blob (≥32 chars, mixed lower+upper+digit so all-hex shas and
# UPPER_CONSTANTS are skipped). Opt-in only: false-positives on the ids/hashes a code brain holds.
_HIGH_ENTROPY = _Pattern(
    "high-entropy",
    re.compile(
        r"\b(?=[A-Za-z0-9+/]*[a-z])(?=[A-Za-z0-9+/]*[A-Z])(?=[A-Za-z0-9+/]*[0-9])"
        r"[A-Za-z0-9+/]{32,}={0,2}\b"
    ),
)


def redact_secrets(text: str, *, include_high_entropy: bool = False) -> RedactionResult:
    """Scrub credential-shaped substrings from ``text``, returning cleaned text + an audit trail.

    Each pattern is applied in turn; matches become ``[REDACTED:<kind>]`` (the key/structure of a
    ``KEY=value`` or URL credential is kept, only the secret value removed).
    ``include_high_entropy`` adds a generic base64-blob sweep — off by default (false-positives on
    code hashes/ids). The result carries kind+count events only, never the captured secret."""
    if not text:
        return RedactionResult(text, ())
    counts: dict[str, int] = {}
    patterns = (*_PATTERNS, _HIGH_ENTROPY) if include_high_entropy else _PATTERNS
    result = text
    for pat in patterns:

        def _sub(
            match: re.Match[str], kind: str = pat.kind, group: int = pat.keep_prefix_group
        ) -> str:
            counts[kind] = counts.get(kind, 0) + 1
            prefix = match.group(group) if group else ""
            return f"{prefix}[REDACTED:{kind}]"

        result = pat.regex.sub(_sub, result)
    events = tuple(RedactionEvent(kind, counts[kind]) for kind in sorted(counts))
    return RedactionResult(result, events)
