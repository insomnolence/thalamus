"""DerivedViews — the gateway's refreshable derived state, bundled for atomic swap.

In a long-running, many-session serve the gateway's *derived* views diverge from
durable truth as writes accumulate: a ``remember --supersedes`` persists a
SUPERSEDES edge to Neo4j but the ``superseded`` map read once at composition never
sees it; a deleted file makes a curated memory's footprint stale but the
precomputed ``stale_references`` map never updates. (The stdio PoC refreshed these
by accident on every ``/mcp`` reconnect; the HTTP-streaming destination has no
reconnect — see the architectural-constraint note.) Dreaming's link-resolution
pass recomputes these from durable truth and swaps them in via ``Gateway.refresh``.

Bundling them into one immutable snapshot behind a single-slot holder gives
copy-on-write for free for read-heavy recall: a reader snapshots ``.views`` once
(one atomic attribute read under the GIL) and reads every field from that local,
so a concurrent refresh — one atomic attribute assignment — is observed whole or
not at all, never as a torn mix of old and new. The holder is the single piece of
state *shared* between the Gateway and the upstream demoting retriever, so one
``refresh`` reaches both.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from thalamus.core.types import MemoryRef, Supersession


@dataclass(frozen=True, slots=True)
class DerivedViews:
    """An immutable snapshot of the gateway's refreshable derived views.

    Treated as a value: a refresh hands over freshly-built maps rather than
    mutating these in place, which is what makes the atomic swap safe.
    """

    superseded: Mapping[MemoryRef, Supersession] = field(default_factory=dict)
    stale_references: Mapping[MemoryRef, Sequence[str]] = field(default_factory=dict)


class DerivedViewsRef:
    """A mutable single-slot holder for the current :class:`DerivedViews` snapshot.

    Readers must snapshot :attr:`views` once per operation into a local;
    :meth:`refresh` swaps the whole bundle with one atomic attribute assignment.
    """

    def __init__(self, views: DerivedViews | None = None) -> None:
        self.views = views if views is not None else DerivedViews()

    def refresh(self, views: DerivedViews) -> None:
        """Atomically replace the current snapshot (a single ``STORE_ATTR``)."""
        self.views = views
