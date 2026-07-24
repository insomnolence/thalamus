"""``thalamus.cli remember`` - store explicitly retained repo knowledge in Brain 1.

Episodes capture what happened; retained memories capture facts an agent should not
have to rediscover: decisions, constraints, gotchas, investigation results, and
preferences. They use the same experiential store and optional footprint links as
episodes, but are explicitly marked as curated in metadata so the payload can present
them separately.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import math
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from thalamus.cli.brain import build_store, close_store
from thalamus.core.exceptions import UserFacingError
from thalamus.core.protocols import Encoder, Store, SupersessionIndex
from thalamus.core.redaction import RedactionEvent, merge_redaction_events, redact_secrets
from thalamus.core.taxonomy import ACCEPTED_KINDS, normalize_kind
from thalamus.core.types import (
    Hemisphere,
    MemoryId,
    MemoryRecord,
    MemoryRef,
    RepoId,
    Scope,
    TenantId,
)
from thalamus.routing import build_encoder

logger = logging.getLogger(__name__)

_DEFAULT_DIM = 128
_DEFAULT_ENCODER = "bge-small"


@dataclass(frozen=True, slots=True)
class RememberConfig:
    repo: Path
    tenant: str
    repo_id: str
    dim: int
    encoder: str
    kind: str
    text: str
    why: str | None
    files: tuple[Path, ...]
    importance: float
    memory_id: str | None
    supersedes: str | None
    neo4j_uri: str | None
    neo4j_user: str
    neo4j_password: str | None
    redact: bool = True  # scrub secrets before embed/store (§17.4 T2); --no-secret-redaction off


def add_remember_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="repository this memory applies to"
    )
    parser.add_argument("--tenant", default="local", help="tenant id for scoping")
    parser.add_argument(
        "--repo-id", default=None, help="repo id for scoping (default: repo dir name)"
    )
    parser.add_argument("--dim", type=int, default=_DEFAULT_DIM, help="embedding dimensionality")
    parser.add_argument(
        "--encoder", choices=("bge-small", "deterministic"), default=_DEFAULT_ENCODER,
        help="embedding model (default: bge-small; deterministic is for smoke tests)",
    )
    parser.add_argument(
        "--kind", choices=ACCEPTED_KINDS, required=True, help="type of retained knowledge"
    )
    parser.add_argument("--text", required=True, help="concise fact the agent should recall")
    parser.add_argument("--why", default=None, help="supporting rationale or evidence")
    parser.add_argument(
        "--file", dest="files", action="append", type=Path, default=[],
        help="repo-relative related file; may be supplied more than once",
    )
    parser.add_argument(
        "--importance", type=float, default=1.0,
        help="fixed baseline priority signal for this fact (default: 1.0)",
    )
    parser.add_argument(
        "--id", dest="memory_id", default=None,
        help="stable id for intentionally updating a fact (default: derived from kind/text)",
    )
    parser.add_argument(
        "--supersedes", default=None,
        help="memory id this fact replaces (§13.18 D1): records a supersession edge so the "
        "old belief is demoted below current truth but kept, surfaced with this fact's why/text "
        "as the reason. Never deletes the old memory.",
    )
    parser.add_argument(
        "--secret-redaction", dest="redact", action=argparse.BooleanOptionalAction, default=True,
        help="scrub credential-shaped text (tokens/keys/passwords) before embed/store (§17.4 T2); "
        "on by default — pass --no-secret-redaction to disable",
    )


def remember_config(args: argparse.Namespace) -> RememberConfig:
    repo = Path(args.repo).resolve()
    return RememberConfig(
        repo=repo,
        tenant=str(args.tenant),
        repo_id=str(args.repo_id) if args.repo_id else repo.name,
        dim=int(args.dim),
        encoder=str(args.encoder),
        kind=str(args.kind),
        text=str(args.text),
        why=str(args.why) if args.why is not None else None,
        files=tuple(Path(item) for item in args.files),
        importance=float(args.importance),
        memory_id=str(args.memory_id) if args.memory_id is not None else None,
        supersedes=str(args.supersedes) if args.supersedes is not None else None,
        neo4j_uri=os.environ.get("THALAMUS_NEO4J_URI"),
        neo4j_user=os.environ.get("THALAMUS_NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("THALAMUS_NEO4J_PASSWORD"),
        redact=bool(args.redact),
    )


def _footprint(config: RememberConfig) -> list[str]:
    """Repo-relative footprint paths. A file outside the repo (or escaping via ``..``) is SKIPPED
    with a warning, never fatal — a stray or out-of-corpus path must not cost the whole memory
    (§14.4: losing the memory is worse than a missing footprint entry). E.g. a multi-root brain
    (a sample project: code root vs. outer doc roots) legitimately cites docs outside the code
    root; those simply don't become footprint entries (which only link to code modules anyway)."""
    footprint: list[str] = []
    for file_path in config.files:
        path = file_path if file_path.is_absolute() else (config.repo / file_path)
        try:
            rel_path = path.resolve().relative_to(config.repo)
        except ValueError:
            logger.warning("related file outside the repository — skipped: %s", file_path)
            continue
        if ".." in rel_path.parts:
            logger.warning("related file escapes the repository — skipped: %s", file_path)
            continue
        footprint.append(rel_path.as_posix())
    return footprint


def build_retained_record(
    config: RememberConfig,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    redact: bool = True,
) -> MemoryRecord:
    """Build an idempotently-addressed curated memory for storage.

    Secrets in the text/why are scrubbed at this boundary (§17.4 T2) **before** the record is built,
    so a token or password an agent saw never reaches the embedding or the store — far easier than
    expunging it after the fact. The memory id keys on the *redacted* text so it stays stable across
    re-remembers. ``redact=False`` is for tests only. Footprint paths are not scrubbed (not secrets,
    and redaction would corrupt the path → its cross-link)."""
    kind = normalize_kind(config.kind)  # synonyms (e.g. project→decision) map to the canonical set
    if not config.text.strip():
        raise UserFacingError("retained memory text must not be empty")
    if not math.isfinite(config.importance):
        raise UserFacingError("importance must be a finite number")
    events: list[RedactionEvent] = []
    text = config.text
    why = config.why
    if redact:
        text_result = redact_secrets(text)
        text = text_result.text
        events.extend(text_result.events)
        if why is not None:
            why_result = redact_secrets(why)
            why = why_result.text
            events.extend(why_result.events)
        events = list(merge_redaction_events(events))  # one entry per kind
        if events:
            logger.warning(
                "redacted %d secret(s) from the memory before storing: %s",
                sum(event.count for event in events),
                ", ".join(event.kind for event in events),
            )
    scope = Scope(TenantId(config.tenant), RepoId(config.repo_id))
    raw_id = config.memory_id
    if raw_id is None:
        digest = hashlib.sha256(f"{kind}\n{text}".encode()).hexdigest()[:16]
        raw_id = f"retained:{digest}"
    elif not raw_id.startswith("retained:"):
        raw_id = f"retained:{raw_id}"
    metadata: dict[str, object] = {
        "source": "curated",
        "importance": config.importance,
        "footprint": _footprint(config),
    }
    if kind != config.kind:
        metadata["requested_kind"] = config.kind  # audit trail: what the caller actually asked for
    if why is not None:
        metadata["why"] = why
    if events:  # auditable coverage — kind+count only, never the redacted secret itself
        metadata["redacted"] = [{"kind": e.kind, "count": e.count} for e in events]
    return MemoryRecord(
        memory_id=MemoryId(raw_id),
        hemisphere=Hemisphere.EXPERIENTIAL,
        kind=kind,
        content=text,
        scope=scope,
        created_at=now(),
        metadata=metadata,
    )


def _resolve_supersedes(scope: Scope, raw: str, store: Store) -> MemoryRef | None:
    """An existing memory matching ``raw``, tolerant of a missing ``retained:`` prefix.

    Agents routinely pass the bare hash from a recall instead of the full ``retained:<hash>``
    id; try the prefixed form too so a correct-but-prefixless reference still resolves."""
    if not raw:
        return None
    candidates = [raw] if raw.startswith("retained:") else [f"retained:{raw}", raw]
    for candidate in candidates:
        ref = MemoryRef(scope, MemoryId(candidate))
        if store.get(ref) is not None:
            return ref
    return None


def _record_supersession(
    config: RememberConfig,
    record: MemoryRecord,
    store: Store,
    supersession: SupersessionIndex | None,
) -> MemoryRef | None:
    """**Best-effort** D1 supersession edge for a freshly-written belief; returns the superseded
    ref, or ``None`` when the edge can't be recorded (logged + skipped).

    The new memory is ALWAYS kept — a bad ``supersedes`` (no storage / unknown target / self)
    never raises, because run_remember has already saved the record: raising here would surface
    an error to the caller that *also* persisted the memory, so an agent retries → duplicates.
    Conservative (§14.4): a dangling edge is skipped, never forged; the old record is untouched."""
    raw = config.supersedes or ""
    if supersession is None:
        logger.warning("supersedes=%r ignored: no durable supersession storage set", raw)
        return None
    old_ref = _resolve_supersedes(record.scope, raw, store)
    if old_ref is None:
        logger.warning("supersedes target %r not found — kept the memory, skipped the edge", raw)
        return None
    if old_ref == record.ref:
        logger.warning("supersedes=%r is the new memory itself — skipped", raw)
        return None
    supersession.supersede(
        old=old_ref, new=record.ref, reason=config.why or config.text, at=record.created_at
    )
    return old_ref


def run_remember(
    config: RememberConfig,
    *,
    store: Store | None = None,
    encoder: Encoder | None = None,
    supersession: SupersessionIndex | None = None,
    announce: bool = True,
) -> MemoryRecord:
    """Persist one explicit retained memory, returning the record written.

    When ``config.supersedes`` is set, also records a §13.18 D1 supersession edge into
    ``supersession`` (the old belief is kept, demoted below current truth at recall)."""
    if store is None and config.neo4j_uri is None:
        raise UserFacingError(
            "remember requires durable Brain 1 storage; "
            "set THALAMUS_NEO4J_URI before writing a memory"
        )
    record = build_retained_record(config, redact=config.redact)
    encoder = encoder or build_encoder(config.encoder, dim=config.dim)
    owned_store = store is None
    if store is None:
        store = build_store(
            dim=encoder.dim,
            neo4j_uri=config.neo4j_uri,
            neo4j_user=config.neo4j_user,
            neo4j_password=config.neo4j_password,
            encoder_id=config.encoder,
        )
    footprint = record.metadata["footprint"]
    assert isinstance(footprint, list)
    # Embed from the (already-redacted) record, never the raw config — otherwise a scrubbed secret
    # would still reach the vector index via the embedding text (§17.4 T2).
    record_why = record.metadata.get("why")
    embedding_text = " ".join(
        value
        for value in (config.kind, record.content, record_why, " ".join(footprint))
        if value
    )
    superseded: MemoryRef | None = None
    try:
        store.add(record, encoder.encode([embedding_text])[0])
        if config.supersedes is not None:
            superseded = _record_supersession(config, record, store, supersession)
    finally:
        if owned_store:
            close_store(store)
    if announce:
        note = f" (supersedes {superseded.memory_id})" if superseded is not None else ""
        print(f"remembered {record.memory_id} ({record.kind}) in Brain 1 [{config.repo_id}]{note}")
    return record
