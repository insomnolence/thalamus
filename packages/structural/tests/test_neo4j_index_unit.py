"""Offline regression coverage for Neo4jStructuralIndex query construction."""

from __future__ import annotations

from typing import Any

import pytest
import thalamus.structural.neo4j_index as neo4j_index
from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.structural import Neo4jStructuralIndex

SCOPE = Scope(TenantId("t"), RepoId("r"))


@pytest.mark.parametrize(
    ("corpus", "quoted_index"),
    [
        ("docs (dollhouse)", "`vec_struct_docs (dollhouse)`"),
        ("docs `quoted`", "`vec_struct_docs ``quoted```"),
    ],
)
def test_corpus_derived_index_name_is_safely_quoted(
    monkeypatch: pytest.MonkeyPatch,
    corpus: str,
    quoted_index: str,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def run(
        driver: object,
        database: str,
        cypher: str,
        **params: Any,
    ) -> list[Any]:
        del driver, database
        calls.append((cypher, params))
        return []

    monkeypatch.setattr(neo4j_index, "_run", run)
    index = Neo4jStructuralIndex(object(), SCOPE, dim=384, corpus=corpus)  # type: ignore[arg-type]

    create, create_params = calls[0]
    assert create.startswith(f"CREATE VECTOR INDEX {quoted_index} IF NOT EXISTS")
    assert create_params == {}

    assert index.search([0.0] * 384, k=1, scope=SCOPE) == []
    vector_query, query_params = next(
        (cypher, params) for cypher, params in calls if "queryNodes" in cypher
    )
    assert "queryNodes($index_name, $fetch_k, $vec)" in vector_query
    assert query_params["index_name"] == f"vec_struct_{corpus}"
