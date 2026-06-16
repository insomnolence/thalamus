"""The context payload — what the actuator receives from the brain.

Good packaging matters as much as retrieval (§5.6). ``render`` assembles a clean
block: experiential memories + their *why*, and — when cross-hemisphere links
exist — the related code symbols from Brain 2 (§13.19). Structural sections will
grow (gotchas, constraints) as Brain 2 gains resolved call/reference edges.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from thalamus.core.types import EventId, MemoryId, ScoredMemory, Supersession
from thalamus.structural.index import ScoredNode
from thalamus.structural.schema import StructuralNode


@dataclass(frozen=True, slots=True)
class SupersededNote:
    """Marks a surfaced memory as superseded (§13.18): current truth ranks above it, but
    it is still shown *with its supersession reason* — the "used X until May, switched to Y
    because Z" view, never silently dropped (§14.4 conservative-against-silent-poisons)."""

    superseded_by: MemoryId
    reason: str
    at: str  # ISO-8601 timestamp of the supersession


@dataclass(frozen=True, slots=True)
class MemoryItem:
    """One experiential memory, flattened for the payload."""

    memory_id: MemoryId
    kind: str
    content: str
    score: float
    why: str | None = None
    source: str | None = None
    stale_references: tuple[str, ...] = ()  # footprint files no longer on disk (§13.18-D2)
    superseded: SupersededNote | None = None  # set iff a newer belief replaced this (§13.18 R1)

    @classmethod
    def from_scored(
        cls,
        scored: ScoredMemory,
        *,
        max_content_chars: int | None = None,
        stale_references: Sequence[str] = (),
        superseded: Supersession | None = None,
    ) -> MemoryItem:
        content = scored.record.content
        rationale = _render_why(scored.record.metadata.get("why"))
        note: SupersededNote | None = None
        if superseded is not None:
            reason = superseded.reason
            if max_content_chars is not None:
                reason = _truncate(reason, max_content_chars)
            note = SupersededNote(
                superseded_by=superseded.superseded_by,
                reason=reason,
                at=superseded.at.isoformat(),
            )
        if max_content_chars is not None:
            content = _truncate(content, max_content_chars)
            if rationale is not None:
                rationale = _truncate(rationale, max_content_chars)
        return cls(
            memory_id=scored.record.memory_id,
            kind=scored.record.kind,
            content=content,
            score=scored.score,
            why=rationale,
            source=str(scored.record.metadata.get("source", "")) or None,
            stale_references=tuple(stale_references),
            superseded=note,
        )

    @property
    def retained(self) -> bool:
        """Whether this is explicitly retained knowledge rather than a derived episode."""
        return self.source == "curated"


def _render_why(why: object) -> str | None:
    """Render a memory's ``why`` to clean text. Curated memories store a string; episodes store a
    structured value (e.g. ``[{'text': …, 'kind': 'goal'}]``) — extract the text rather than dumping
    the Python repr into the payload."""
    if why is None:
        return None
    if isinstance(why, str):
        return why or None
    if isinstance(why, Mapping):
        text = why.get("text")
        return str(text) if text else None
    if isinstance(why, (list, tuple)):
        parts: list[str] = []
        for item in why:
            if isinstance(item, Mapping) and item.get("text"):
                parts.append(str(item["text"]))
            elif isinstance(item, str) and item:
                parts.append(item)
        return "; ".join(parts) or None
    return str(why) or None


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    suffix = "..." if limit >= 3 else ""
    return value[: limit - len(suffix)].rstrip() + suffix


def _node_location(node: StructuralNode) -> str | None:
    if node.anchor is None:
        return None
    anchor = node.anchor
    return f"{anchor.path}:{anchor.line_start}-{anchor.line_end}"


@dataclass(frozen=True, slots=True)
class StructuralItem:
    """A structural node in the payload — via a cross-hemisphere link (§13.19) or, when
    ``relevance`` is set, a direct semantic hit from structural retrieval."""

    node_id: str
    kind: str
    label: str
    location: str | None = None
    relevance: float | None = None  # set for direct structural hits; None for linked nodes
    corpus: str = "code"  # which Brain-2 corpus (code / docs / …) — groups the payload section

    @classmethod
    def from_node(cls, node: StructuralNode, *, corpus: str = "code") -> StructuralItem:
        return cls(
            node_id=node.node_id,
            kind=node.kind,
            label=node.label,
            location=_node_location(node),
            corpus=corpus,
        )

    @classmethod
    def from_scored_node(cls, scored: ScoredNode, *, corpus: str = "code") -> StructuralItem:
        node = scored.node
        return cls(
            node_id=node.node_id,
            kind=node.kind,
            label=node.label,
            location=_node_location(node),
            relevance=scored.score,
            corpus=corpus,
        )


@dataclass(frozen=True, slots=True)
class CallRelation:
    """A surfaced code symbol with its direct callers and callees (the Brain-2 call graph).

    Answers "what breaks if I change this" (callers) and "what does this use" (callees)
    for the symbols a cue is about — the §13.19 call graph made visible at recall time.
    """

    label: str
    callers: tuple[str, ...]
    callees: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContextPayload:
    """The assembled context returned for a cue."""

    cue_text: str
    memories: Sequence[MemoryItem]
    structural: Sequence[StructuralItem] = ()
    structural_omitted: int = 0
    calls: Sequence[CallRelation] = ()
    event_id: EventId | None = None  # correlate later outcome (usage) signals to this recall

    def render(self) -> str:
        """Render the payload as a clean text block for the actuator."""
        if not self.memories and not self.structural and not self.calls:
            return f"# Context for: {self.cue_text}\n\n(no relevant memories)\n"
        lines = [f"# Context for: {self.cue_text}"]
        retained = [item for item in self.memories if item.retained]
        experience = [item for item in self.memories if not item.retained]
        for heading, items in (("Retained memory", retained), ("Prior episodes", experience)):
            if not items:
                continue
            lines += ["", f"## {heading}"]
            for item in items:
                superseded = " [superseded]" if item.superseded else ""
                lines.append(
                    f"- ({item.kind}, relevance {item.score:.2f}){superseded} {item.content}"
                )
                if item.why:
                    lines.append(f"  why: {item.why}")
                if item.superseded is not None:
                    note = item.superseded
                    lines.append(
                        f"  ⊘ superseded by {note.superseded_by} on {note.at}: {note.reason}"
                    )
                if item.stale_references:
                    gone = ", ".join(item.stale_references)
                    lines.append(f"  ⚠ may be stale — references no longer in the codebase: {gone}")
        if self.structural or self.structural_omitted:
            by_corpus: dict[str, list[StructuralItem]] = {}
            for symbol in self.structural:
                by_corpus.setdefault(symbol.corpus, []).append(symbol)
            for corpus, symbols in by_corpus.items():
                lines += ["", f"## Related {corpus}"]
                for symbol in symbols:
                    location = f" — {symbol.location}" if symbol.location else ""
                    relevance = (
                        f" [relevance {symbol.relevance:.2f}]"
                        if symbol.relevance is not None
                        else ""
                    )
                    lines.append(f"- ({symbol.kind}) {symbol.label}{location}{relevance}")
            if self.structural_omitted:
                lines.append(f"- ... {self.structural_omitted} additional related item(s) omitted")
        if self.calls:
            lines += ["", "## Call graph"]
            for relation in self.calls:
                lines.append(f"- {relation.label}")
                if relation.callers:
                    lines.append(f"    called by: {', '.join(relation.callers)}")
                if relation.callees:
                    lines.append(f"    calls: {', '.join(relation.callees)}")
        return "\n".join(lines) + "\n"
