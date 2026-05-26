"""Tests for direct structural retrieval (thalamus.structural.index)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from thalamus.core.exceptions import DimensionMismatchError
from thalamus.core.types import Cue, RepoId, Scope, TenantId, Vector
from thalamus.structural.index import InMemoryStructuralIndex, StructuralRetriever, node_text
from thalamus.structural.schema import StructuralNode

SCOPE = Scope(TenantId("acme"), RepoId("widgets"))
OTHER = Scope(TenantId("acme"), RepoId("other"))


class _FixedEncoder:
    """Encoder returning a constant vector (asserts the retriever encodes the cue)."""

    def __init__(self, vec: Sequence[float]) -> None:
        self._vec = list(vec)

    @property
    def dim(self) -> int:
        return len(self._vec)

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        return [list(self._vec) for _ in texts]


class _RaisingEncoder:
    """Encoder that fails if asked to encode (asserts the cue embedding is reused)."""

    def __init__(self, dim: int) -> None:
        self._dim = dim
        self.calls = 0

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        self.calls += 1
        raise AssertionError("encode should not be called when the cue carries an embedding")


def _node(
    node_id: str, label: str, *, scope: Scope = SCOPE, kind: str = "function"
) -> StructuralNode:
    return StructuralNode(node_id=node_id, kind=kind, label=label, scope=scope)


def test_node_text_includes_qualified_path() -> None:
    assert node_text(_node("function:pkg.mod.func", "func")) == "function func (pkg.mod.func)"


def test_node_text_without_redundant_qualifier() -> None:
    assert node_text(_node("module:foo", "foo", kind="module")) == "module foo"


def test_search_orders_by_cosine_and_filters_scope() -> None:
    index = InMemoryStructuralIndex(dim=3)
    index.add(_node("function:m.a", "a"), [1.0, 0.0, 0.0])
    index.add(_node("function:m.b", "b"), [0.0, 1.0, 0.0])
    index.add(_node("function:o.c", "c", scope=OTHER), [1.0, 0.0, 0.0])  # other scope, excluded

    hits = index.search([1.0, 0.0, 0.0], k=5, scope=SCOPE)
    assert [h.node.node_id for h in hits] == ["function:m.a", "function:m.b"]
    assert hits[0].score > hits[1].score
    assert hits[0].features["relevance"] == hits[0].score


def test_search_respects_k() -> None:
    index = InMemoryStructuralIndex(dim=3)
    index.add(_node("function:m.a", "a"), [1.0, 0.0, 0.0])
    index.add(_node("function:m.b", "b"), [0.0, 1.0, 0.0])
    assert len(index.search([1.0, 0.0, 0.0], k=1, scope=SCOPE)) == 1


def test_add_dim_mismatch_raises() -> None:
    index = InMemoryStructuralIndex(dim=3)
    with pytest.raises(DimensionMismatchError):
        index.add(_node("function:m.a", "a"), [1.0, 0.0])


def test_retriever_encodes_cue_text() -> None:
    index = InMemoryStructuralIndex(dim=3)
    index.add(_node("function:m.a", "a"), [1.0, 0.0, 0.0])
    index.add(_node("function:m.b", "b"), [0.0, 1.0, 0.0])
    retriever = StructuralRetriever(_FixedEncoder([1.0, 0.0, 0.0]), index)
    hits = retriever.retrieve(Cue(text="find a", scope=SCOPE), k=1)
    assert [h.node.node_id for h in hits] == ["function:m.a"]


def test_retriever_reuses_cue_embedding() -> None:
    index = InMemoryStructuralIndex(dim=3)
    index.add(_node("function:m.a", "a"), [1.0, 0.0, 0.0])
    index.add(_node("function:m.b", "b"), [0.0, 1.0, 0.0])
    encoder = _RaisingEncoder(dim=3)
    retriever = StructuralRetriever(encoder, index)
    hits = retriever.retrieve(Cue(text="ignored", scope=SCOPE, embedding=[0.0, 1.0, 0.0]), k=1)
    assert [h.node.node_id for h in hits] == ["function:m.b"]
    assert encoder.calls == 0
