"""The context payload — what the actuator receives from the brain.

Good packaging matters as much as retrieval (§5.6). ``render`` assembles a clean
block: experiential memories + their *why*, and — when cross-hemisphere links
exist — the related code symbols from Brain 2 (§13.19). Structural sections will
grow (gotchas, constraints) as Brain 2 gains resolved call/reference edges.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from thalamus.core.types import EventId, MemoryId, ScoredMemory
from thalamus.structural.schema import StructuralNode


@dataclass(frozen=True, slots=True)
class MemoryItem:
    """One experiential memory, flattened for the payload."""

    memory_id: MemoryId
    kind: str
    content: str
    score: float
    why: str | None = None

    @classmethod
    def from_scored(cls, scored: ScoredMemory) -> MemoryItem:
        why = scored.record.metadata.get("why")
        return cls(
            memory_id=scored.record.memory_id,
            kind=scored.record.kind,
            content=scored.record.content,
            score=scored.score,
            why=str(why) if why is not None else None,
        )


@dataclass(frozen=True, slots=True)
class StructuralItem:
    """A structural node surfaced via a cross-hemisphere link (§13.19)."""

    node_id: str
    kind: str
    label: str
    location: str | None = None

    @classmethod
    def from_node(cls, node: StructuralNode) -> StructuralItem:
        location: str | None = None
        if node.anchor is not None:
            anchor = node.anchor
            location = f"{anchor.path}:{anchor.line_start}-{anchor.line_end}"
        return cls(node_id=node.node_id, kind=node.kind, label=node.label, location=location)


@dataclass(frozen=True, slots=True)
class ContextPayload:
    """The assembled context returned for a cue."""

    cue_text: str
    memories: Sequence[MemoryItem]
    structural: Sequence[StructuralItem] = ()
    event_id: EventId | None = None  # correlate later outcome (usage) signals to this recall

    def render(self) -> str:
        """Render the payload as a clean text block for the actuator."""
        if not self.memories and not self.structural:
            return f"# Context for: {self.cue_text}\n\n(no relevant memories)\n"
        lines = [f"# Context for: {self.cue_text}"]
        if self.memories:
            lines += ["", "## Relevant experience"]
            for item in self.memories:
                lines.append(f"- ({item.kind}, relevance {item.score:.2f}) {item.content}")
                if item.why:
                    lines.append(f"  why: {item.why}")
        if self.structural:
            lines += ["", "## Related code"]
            for symbol in self.structural:
                location = f" — {symbol.location}" if symbol.location else ""
                lines.append(f"- ({symbol.kind}) {symbol.label}{location}")
        return "\n".join(lines) + "\n"
