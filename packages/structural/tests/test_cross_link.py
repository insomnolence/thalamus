from __future__ import annotations

from thalamus.core.types import MemoryId
from thalamus.structural import InMemoryCrossLinkIndex


def test_link_and_lookup() -> None:
    index = InMemoryCrossLinkIndex()
    index.link(MemoryId("m1"), "function:pkg.mod.f")
    index.link(MemoryId("m1"), "class:pkg.mod.C")
    index.link(MemoryId("m2"), "function:pkg.mod.f")

    assert index.nodes_for(MemoryId("m1")) == ["function:pkg.mod.f", "class:pkg.mod.C"]
    assert set(index.memories_for("function:pkg.mod.f")) == {MemoryId("m1"), MemoryId("m2")}
    assert index.nodes_for(MemoryId("missing")) == []
    assert index.memories_for("nope") == []


def test_link_is_deduped() -> None:
    index = InMemoryCrossLinkIndex()
    index.link(MemoryId("m1"), "n")
    index.link(MemoryId("m1"), "n")
    assert index.nodes_for(MemoryId("m1")) == ["n"]
    assert index.memories_for("n") == [MemoryId("m1")]
