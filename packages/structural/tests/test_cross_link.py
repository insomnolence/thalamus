from __future__ import annotations

from thalamus.core.types import MemoryId, MemoryRef, RepoId, Scope, StructuralRef, TenantId
from thalamus.structural import InMemoryCrossLinkIndex

SCOPE = Scope(TenantId("t"), RepoId("r"))


def test_link_and_lookup() -> None:
    index = InMemoryCrossLinkIndex()
    m1, m2 = MemoryRef(SCOPE, MemoryId("m1")), MemoryRef(SCOPE, MemoryId("m2"))
    func, cls = StructuralRef(SCOPE, "function:pkg.mod.f"), StructuralRef(SCOPE, "class:pkg.mod.C")
    index.link(m1, func)
    index.link(m1, cls)
    index.link(m2, func)

    assert index.nodes_for(m1) == [func, cls]
    assert set(index.memories_for(func)) == {m1, m2}
    assert index.nodes_for(MemoryRef(SCOPE, MemoryId("missing"))) == []
    assert index.memories_for(StructuralRef(SCOPE, "nope")) == []


def test_link_is_deduped() -> None:
    index = InMemoryCrossLinkIndex()
    memory, node = MemoryRef(SCOPE, MemoryId("m1")), StructuralRef(SCOPE, "n")
    index.link(memory, node)
    index.link(memory, node)
    assert index.nodes_for(memory) == [node]
    assert index.memories_for(node) == [memory]
