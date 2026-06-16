"""CoChangeRefreshPass — keep the plan tool's file co-change index fresh during a long serve.

The structural sibling of :class:`UsageRefreshPass`. The plan tool's blast radius fuses call-graph
reachability with *file co-change* (symbols whose files historically change together); that index is
built once at serve startup and otherwise goes stale as new commits land. Each maintenance tick this
actor **recomputes** the index from current git history + the live graph and **swaps** it into the
planner's holder through the injected ``refresh`` seam — so new coupling becomes visible without a
restart.

Both seams are injected (the composition root closes over the repo + graph for ``recompute`` and
over ``CoChangeRef.refresh``), so ``dreaming`` never runs git or imports the builder — it stays pure
orchestration. Deterministic over git history ⇒ it may *act* (§14.3 firewall); the signal is a
behavioral act (developers changed these files together), never the model grading its own output.
"""

from __future__ import annotations

from collections.abc import Callable, Sized

from thalamus.dreaming.base import PassContext, PassKind, PassOutcome
from thalamus.structural import CoChangeIndex


class CoChangeRefreshPass:
    """Recompute the file co-change index from current history and swap it into the planner."""

    name = "cochange-refresh"
    kind = PassKind.ACTOR

    def __init__(
        self,
        recompute: Callable[[], CoChangeIndex],
        refresh: Callable[[CoChangeIndex], None],
    ) -> None:
        self._recompute = recompute
        self._refresh = refresh

    def run(self, ctx: PassContext) -> PassOutcome:
        index = self._recompute()
        self._refresh(index)
        n = len(index) if isinstance(index, Sized) else 0
        return PassOutcome(
            summary=f"refreshed the plan co-change index: {n} file(s) with coupling",
            details={"files": n},
        )
