"""Compose the retrieval-rung ablation arms — one source of truth for every eval that ablates them.

The ablation switch (OLR §13.20) needs the *same* named arms wherever it's run: the brain-off floor,
the brain-on relevance base, and each removable rung layered over it — L-R1 usage, L-R2 query-local
structural relevance, L-R2 global centrality, and the full stack. ``probe-eval --rungs`` (surface
metric) and ``rung-eval`` (usage-join metric) both compose them here so an arm means the same thing
in both. Brain-2 arms are added only when the collaborators are present (experiential-only → just
the floor / base / usage). Rungs are firewall-clean (behavioral / structural signal, not prose)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from thalamus.core.protocols import Retriever
from thalamus.core.types import MemoryId, MemoryRef, Supersession
from thalamus.eval import NullRetriever
from thalamus.gateway import StructuralRelevanceRetriever, SupersededDemotingRetriever
from thalamus.retrieval import (
    CentralityWeightsRef,
    StructuralCentralityRetriever,
    UsageWeightedRetriever,
    UsageWeightsRef,
)
from thalamus.structural import CrossLinkIndex, StructuralRetriever


def compose_rung_arms(
    base: Retriever,
    superseded: Mapping[MemoryRef, Supersession],
    *,
    usage_weights: Mapping[MemoryRef | MemoryId, float] | None = None,
    usage_weight: float = 1.0,
    links: CrossLinkIndex | None = None,
    structural_retrievers: Sequence[StructuralRetriever] = (),
    centrality: CentralityWeightsRef | None = None,
) -> dict[str, Retriever]:
    """The named ablation arms over a relevance ``base``, each supersession-demoted (current truth).

    ``brain-off`` / ``brain-on`` always present; ``brain-on+usage`` when usage weights are given;
    the structural arms (``+structrel`` / ``+central`` / ``+full``) only when ``links`` +
    ``centrality`` are present (they need Brain 2). ``usage_weight`` tunes the usage rung's RRF
    strength (a code-rich sample project's verdict: 1.0 over-promotes and hurts recall@k).
    ``+full`` mirrors the LIVE stack — usage → central, NOT structrel (which earned nothing
    in both brains) — so it measures what ships; ``+structrel`` stays a standalone diagnostic
    arm to keep re-confirming it."""
    arms: dict[str, Retriever] = {
        "brain-off": NullRetriever(),
        "brain-on": SupersededDemotingRetriever(base, superseded),
    }

    def _usage(inner: Retriever) -> Retriever:
        ref = UsageWeightsRef(dict(usage_weights or {}))
        return UsageWeightedRetriever(inner, ref, weight=usage_weight)

    if usage_weights:
        arms["brain-on+usage"] = SupersededDemotingRetriever(_usage(base), superseded)
    if links is not None and centrality is not None:
        arms["brain-on+structrel"] = SupersededDemotingRetriever(
            StructuralRelevanceRetriever(base, links, structural_retrievers), superseded
        )
        arms["brain-on+central"] = SupersededDemotingRetriever(
            StructuralCentralityRetriever(base, centrality), superseded
        )
        full = _usage(base) if usage_weights else base
        full = StructuralCentralityRetriever(full, centrality)  # central outermost = winner leads
        arms["brain-on+full"] = SupersededDemotingRetriever(full, superseded)
    return arms


__all__ = ["compose_rung_arms"]
