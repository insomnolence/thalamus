from __future__ import annotations

import json
from pathlib import Path

from thalamus.core.types import EventId, MemoryId
from thalamus.instrumentation import (
    InMemoryUsageSink,
    JsonlUsageSink,
    UsageSignal,
    attribute_declared,
    attribute_overlap,
    serialize_usage,
)


def test_attribute_overlap_detects_use() -> None:
    shown = [
        (MemoryId("m_used"), "use aiosqlite for the async store"),
        (MemoryId("m_unused"), "prefer terse commit messages"),
    ]
    output = "import aiosqlite\nclass AsyncStore: ...  # the async store connects"
    signals = {s.memory_id: s for s in attribute_overlap(EventId("e1"), shown, output)}

    assert signals[MemoryId("m_used")].used is True
    assert signals[MemoryId("m_used")].value > 0.5
    assert signals[MemoryId("m_unused")].used is False
    assert signals[MemoryId("m_unused")].value == 0.0
    assert all(s.event_id == EventId("e1") for s in signals.values())
    # output-overlap is the secondary citation signal, not the deterministic primary
    assert all(s.kind == "citation" for s in signals.values())


def test_in_memory_sink() -> None:
    sink = InMemoryUsageSink()
    sink.emit(UsageSignal(EventId("e"), MemoryId("m"), "overlap", 1.0, True))
    assert len(sink.signals) == 1


def test_jsonl_sink(tmp_path: Path) -> None:
    sink = JsonlUsageSink(tmp_path / "usage.jsonl")
    sink.emit(UsageSignal(EventId("e1"), MemoryId("m1"), "overlap", 0.75, True))
    obj = json.loads((tmp_path / "usage.jsonl").read_text(encoding="utf-8").strip())
    assert obj["event_id"] == "e1"
    assert obj["used"] is True
    assert obj["value"] == 0.75


def test_serialize_is_json_safe() -> None:
    payload = serialize_usage(UsageSignal(EventId("e"), MemoryId("m"), "overlap", 0.5, True))
    assert json.loads(json.dumps(payload))["memory_id"] == "m"


# --- the declared-use signal ------------------------------------------------------------------

_SHOWN = [
    (MemoryId("m_a"), "the encoder swap requires rebuilding every index"),
    (MemoryId("m_b"), "prefer terse commit messages"),
    (MemoryId("m_c"), "supersession demotes replaced beliefs"),
]


def test_attribute_declared_credits_named_memories_and_records_the_rest_as_negatives() -> None:
    signals = {s.memory_id: s for s in attribute_declared(EventId("e1"), _SHOWN, [MemoryId("m_a")])}

    assert signals[MemoryId("m_a")].used is True
    assert signals[MemoryId("m_a")].value == 1.0
    # Every shown memory gets a row: the non-declared ones are real within-event NEGATIVES,
    # which a threshold-on-a-continuous-score signal never produces cleanly.
    assert set(signals) == {MemoryId("m_a"), MemoryId("m_b"), MemoryId("m_c")}
    assert signals[MemoryId("m_b")].used is False
    assert signals[MemoryId("m_c")].value == 0.0
    assert all(s.kind == "declared" for s in signals.values())


def test_attribute_declared_ignores_an_id_the_event_never_surfaced() -> None:
    """A stale or invented id contributes nothing rather than fabricating a row."""
    signals = attribute_declared(EventId("e1"), _SHOWN, [MemoryId("m_a"), MemoryId("m_ghost")])

    assert {s.memory_id for s in signals} == {MemoryId("m_a"), MemoryId("m_b"), MemoryId("m_c")}
    assert sum(s.used for s in signals) == 1


def test_attribute_declared_with_nothing_declared_is_all_negative() -> None:
    signals = attribute_declared(EventId("e1"), _SHOWN, [])

    assert len(signals) == 3
    assert not any(s.used for s in signals)


def test_attribute_overlap_cannot_discriminate_at_realistic_memory_length() -> None:
    """Documents WHY the citation signal is retained only as an ablation baseline.

    The toy case above passes because a 7-token memory is reproduced almost verbatim. Real
    memories run 2-4k chars and genuine use paraphrases rather than quotes, so the overlap
    fraction collapses far below any useful threshold — measured on real dogfood pairs, used
    and unused memories were indistinguishable. This test fails if someone "fixes" the signal
    by lowering the threshold, which would make it fire on unused memories too."""
    # Lexical diversity matters, not raw length: `_tokens` is a set, so a repeated paragraph
    # adds no denominator. Real memories carry a few hundred distinct terms; these mirror that.
    used_memory = (
        "SUPERSESSION DEMOTION SHIPPED (commit 4f21ac9, gate green 612 passed). Replaced "
        "beliefs are retained but ranked beneath current ones, so an obsolete migration plan "
        "never outranks the decision that superseded it. Implemented as a flat penalty applied "
        "during the gather phase rather than a hard filter at query time, because discarding "
        "the prior belief would destroy the audit trail explaining how the present one arose. "
        "Threaded through PlannerConfig.gather_supersession_penalty and the L0 rescore path; "
        "annotated inline with an arrow glyph plus the replacing identifier and timestamp. "
        "Deferred: transitive chains collapse to their newest member only, and cross-repository "
        "supersession remains unhandled pending a scope-aware traversal."
    )
    unused_memory = (
        "RECALL HOTPATH CACHING (commit 8be0114). The cue embedding is computed once per "
        "request and shared between the structural, lexical, and vector legs instead of being "
        "recomputed per retriever, removing three redundant ONNX inferences from every call. "
        "Measured 41ms saved at the median on the dollhouse corpus. Also partitions the vector "
        "scan by tenant scope, turning a linear sweep over every stored node into a bounded "
        "lookup, and memoizes norms behind an atomic swap so concurrent readers never observe "
        "a half-rebuilt table. Benchmarked against a synthetic thousand-node fixture."
    )
    shown = [(MemoryId("m_used"), used_memory), (MemoryId("m_unused"), unused_memory)]
    # An output that genuinely *used* the first memory — a paraphrase, as real use looks.
    output = (
        "I kept the old decision visible but ranked it beneath the newer one, so the current "
        "belief wins without discarding the history that explains it."
    )

    signals = {s.memory_id: s for s in attribute_overlap(EventId("e1"), shown, output)}

    assert signals[MemoryId("m_used")].used is False  # genuine use is NOT detected
    assert signals[MemoryId("m_used")].value < 0.2
    # ...and it barely separates from a memory that was not used at all.
    separation = signals[MemoryId("m_used")].value - signals[MemoryId("m_unused")].value
    assert separation < 0.2
