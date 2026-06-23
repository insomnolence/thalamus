"""``thalamus m1a-draft`` — draft an M-1a case from a running brain.

The brain→cases bridge. Given a task and the id of a candidate *decisive* memory, it
recalls against a real brain (read-only) and renders the four arm context blocks from
the actually-surfaced memories:

- ``full``             — the recalled brief (the decisive memory present)
- ``content_ablation`` — the same brief minus the one decisive memory
- ``off``              — a no-memory placebo
- ``salience``         — ``off`` + a generic content-free nudge

It emits a **draft** case (JSON) for a human to vet against the pre-registration's
anti-circularity bars (independent task cue, episode/why not a warning-shaped answer
key, headroom) and to author the deterministic oracle. The draft is never run as-is —
see ``docs/eval/m1a.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from thalamus.cli.brain import build_store, close_store
from thalamus.core.exceptions import ThalamusError
from thalamus.core.types import Cue, MemoryRecord, RepoId, Scope, TenantId
from thalamus.routing import build_encoder

_SALIENCE_NUDGE = "Watch out for known gotchas and project-specific constraints."
_NO_CONTEXT = "(No specific prior project context is provided.)"


@dataclass(frozen=True)
class M1aDraftConfig:
    neo4j_uri: str | None
    neo4j_user: str
    neo4j_password: str | None
    encoder: str
    dim: int
    tenant: str
    repo_id: str
    task: str
    memory_id: str
    case_set: str
    k: int


def add_m1a_draft_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", required=True, help="the task cue (from an INDEPENDENT source)")
    parser.add_argument(
        "--memory-id", required=True, help="id of the candidate decisive memory (must be recalled)"
    )
    parser.add_argument("--repo-id", required=True, help="brain scope: repo id (e.g. dollhouse)")
    parser.add_argument("--tenant", default="local", help="brain scope: tenant (default local)")
    parser.add_argument("--set", dest="case_set", default="positive",
                        choices=("positive", "negative_control", "adversarial"))
    parser.add_argument("--k", type=int, default=8, help="recall depth (default 8)")
    parser.add_argument("--encoder", default="bge-small", help="encoder (default bge-small)")
    parser.add_argument("--dim", type=int, default=384, help="encoder dim (bge-small = 384)")
    parser.add_argument("--neo4j-uri", default=os.environ.get("THALAMUS_NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("THALAMUS_NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-password", default=os.environ.get("THALAMUS_NEO4J_PASSWORD"))


def m1a_draft_config(args: argparse.Namespace) -> M1aDraftConfig:
    return M1aDraftConfig(
        neo4j_uri=args.neo4j_uri,
        neo4j_user=args.neo4j_user,
        neo4j_password=args.neo4j_password,
        encoder=args.encoder,
        dim=int(args.dim),
        tenant=args.tenant,
        repo_id=args.repo_id,
        task=args.task,
        memory_id=args.memory_id,
        case_set=args.case_set,
        k=int(args.k),
    )


def _why_text(metadata: Any) -> str | None:
    why = metadata.get("why") if isinstance(metadata, dict) else None
    if why is None:
        return None
    if isinstance(why, str):
        return why.strip() or None
    if isinstance(why, list):
        parts = [str(item.get("text", "")) for item in why if isinstance(item, dict)]
        joined = " ".join(part for part in parts if part).strip()
        return joined or None
    return str(why)


def render_memory(record: MemoryRecord) -> str:
    line = f"- [{record.kind}] {record.content.strip()}"
    why = _why_text(record.metadata)
    if why:
        line += f"\n  (why: {why})"
    return line


def render_brief(records: Sequence[MemoryRecord]) -> str:
    if not records:
        return _NO_CONTEXT
    body = "\n".join(render_memory(record) for record in records)
    return f"Relevant prior decisions and notes from the project's memory:\n{body}"


def _slug(memory_id: str, task: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")[:40] or "case"
    suffix = re.sub(r"[^a-z0-9]+", "", memory_id.lower())[-8:]
    return f"{base}-{suffix}" if suffix else base


def build_draft(config: M1aDraftConfig, shown: Sequence[MemoryRecord]) -> dict[str, Any]:
    decisive = [r for r in shown if r.memory_id == config.memory_id]
    others = [r for r in shown if r.memory_id != config.memory_id]
    full = render_brief(list(shown))
    ablation = render_brief(others)
    off = _NO_CONTEXT
    salience = f"{off} {_SALIENCE_NUDGE}"
    review = [
        "DRAFT — vet before running (pre-registration bars):",
        "  - author a deterministic oracle (replace the TODO required/forbidden patterns)",
        "  - confirm the task cue is from an INDEPENDENT source (not co-authored with the gotcha)",
        "  - confirm the decisive memory is an episode/why/decision, not a warning-shaped answer",
        "  - confirm headroom: a fresh actuator would NOT already take the right action without it",
    ]
    if not decisive:
        review.insert(
            1,
            f"  - WARNING: memory {config.memory_id!r} was NOT in the top-{config.k} recall — the "
            "brain would not surface it; invalid as a positive case (raise --k or rethink).",
        )
    return {
        "id": _slug(config.memory_id, config.task),
        "set": config.case_set,
        "memory_id": config.memory_id,
        "task": config.task,
        "arms": {"off": off, "salience": salience, "content_ablation": ablation, "full": full},
        "oracle": {"required": ["TODO-author-a-deterministic-check"], "forbidden": []},
        "_review": review,
    }


def run_m1a_draft(config: M1aDraftConfig) -> int:
    if config.neo4j_uri is None:
        raise ThalamusError("m1a-draft needs a brain: set --neo4j-uri (or THALAMUS_NEO4J_URI)")
    scope = Scope(TenantId(config.tenant), RepoId(config.repo_id))
    encoder = build_encoder(config.encoder, dim=config.dim)
    store = build_store(
        dim=encoder.dim,
        neo4j_uri=config.neo4j_uri,
        neo4j_user=config.neo4j_user,
        neo4j_password=config.neo4j_password,
        encoder_id=config.encoder,
    )
    try:
        from thalamus.retrieval import L0Retriever

        retriever = L0Retriever(encoder, store)
        result = retriever.retrieve(Cue(text=config.task, scope=scope), config.k)
        shown = [scored.record for scored in result.shown]
    finally:
        close_store(store)

    print(f"# recalled {len(shown)} memory(ies) for the task (top-{config.k})", flush=True)
    draft = build_draft(config, shown)
    print(json.dumps([draft], indent=2))
    return 0
