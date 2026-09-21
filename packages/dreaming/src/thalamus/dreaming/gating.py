"""Pass gating — skip a pass whose inputs have not moved since its last successful run.

The scheduler has always described its passes as "individually-gated"; this is that gate, built
as a **decorator** so the pass bodies are untouched and the layer is removable in the §14 sense:
unwrap (or pass ``enabled=False``) and the behaviour is exactly the ungated cycle.

**A gate is a correctness claim, not an optimization.** It asserts that the token captures every
input the pass reads. Miss one and the pass silently serves a stale answer — the failure mode that
produced the cross-hemisphere link bug, where a cache keyed on "already done" outlived the
assumption that made it true. Three rules follow, and they are enforced here rather than left to
the caller's discipline:

* **Fail open.** A token function returning ``None`` (cannot tell) runs the pass.
* **Self-heal.** Every ``force_every`` invocations the gate is ignored and the pass runs anyway,
  so a token that turns out to be incomplete converges within a bounded window instead of
  staying wrong until someone notices.
* **Only on success.** The token is recorded only when the pass reports ``OK``; a failed or
  self-skipped pass is retried next cycle.

**What may be gated.** Start with passes whose output nothing behavioral consumes — where a wrong
token costs a stale *log record*, not a stale answer served to the actuator. That property is
established by reading the pass's consumers, not by its :class:`PassKind`: ``credibility`` is
labelled ``ACTOR`` but today only reports a distribution, so it qualifies. A pass whose output
feeds recall (the centrality or usage rungs, the served views) needs its token validated by
``equivalence.check_convergence`` before it is gated at all.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Sequence

from thalamus.core import Scope
from thalamus.dreaming.base import (
    DreamingPass,
    PassContext,
    PassKind,
    PassOutcome,
    PassStatus,
)
from thalamus.dreaming.equivalence import digest
from thalamus.structural import FileManifest

#: Renders the pass's inputs to a comparable token. ``None`` means "cannot tell" -> run.
ChangeToken = Callable[[PassContext], str | None]


def brain1_token(ctx: PassContext) -> str:
    """A token over the whole of Brain 1 for this cycle.

    Reads :meth:`PassContext.memories`, which the cycle has already paid for, so this is a digest
    over records in hand rather than a second enumeration. Deliberately whole-store rather than
    per-pass-subset: over-invalidating costs one extra run, under-invalidating serves a stale
    answer, and the two are not symmetric.
    """
    return digest(ctx.memories())


def file_state_token(paths: Sequence[str]) -> ChangeToken:
    """A token over the size + mtime of each path — for passes that read log files.

    Size and mtime rather than content hash because these are append-only, multi-megabyte logs and
    the point is to be cheaper than the pass. A file that is rewritten in place to exactly the same
    size within one mtime tick would be missed; ``force_every`` covers that.
    """

    def token(ctx: PassContext) -> str | None:
        parts: list[str] = []
        for path in paths:
            try:
                stat = os.stat(path)
                parts.append(f"{path}:{stat.st_size}:{stat.st_mtime_ns}")
            except FileNotFoundError:
                parts.append(f"{path}:absent")
            except OSError:
                return None  # cannot tell -> run
        return digest(parts)

    return token


def file_digest_token(paths: Sequence[str], *, chunk: int = 1 << 20) -> ChangeToken:
    """A token over each path's *contents* — for a file that is rewritten in place.

    ``file_state_token`` is defeated by a derived file that is deleted and rewritten every cycle
    with identical content (``usage_attributed.jsonl`` is, by design: §14.1 says a derived view is
    overwritten, never appended). Its mtime moves every tick, so an mtime token never settles and
    the gate never fires. Hashing the bytes costs a streamed read — still far cheaper than the
    pass, which reads *and* parses the same file.
    """

    def token(ctx: PassContext) -> str | None:
        parts: list[str] = []
        for path in paths:
            sha = hashlib.sha256()
            try:
                with open(path, "rb") as handle:
                    while block := handle.read(chunk):
                        sha.update(block)
            except FileNotFoundError:
                parts.append(f"{path}:absent")
                continue
            except OSError:
                return None  # cannot tell -> run
            parts.append(f"{path}:{sha.hexdigest()}")
        return digest(parts)

    return token


def manifest_token(manifest: FileManifest, scope: Scope) -> ChangeToken:
    """A token over Brain 2's derivation state — the file manifest, read from durable storage.

    The manifest records ``path -> sha256`` for every corpus file of the last build, and
    ``incremental_ingest`` rewrites it only when it actually rebuilt. So its digest changes
    exactly when Brain 2 changed, which makes it a **complete** signal rather than a proxy.

    Crucially it is *durable and shared*: an in-process counter is blind to another process
    rebuilding the same brain (a second serve, a manual ``thalamus dream`` against a live one),
    which would leave a consumer holding a stale graph until the forced run. Every writer updates
    this manifest, so every reader sees it.

    ``node_ids`` are ignored — they are a function of the content hash, so hashing paths and
    shas is both sufficient and cheaper.
    """

    def token(ctx: PassContext) -> str | None:
        try:
            entries = manifest.load(scope)
        except Exception:
            return None  # cannot tell -> run
        return digest(sorted((path, entry.sha256) for path, entry in entries.items()))

    return token


def combine(*tokens: ChangeToken) -> ChangeToken:
    """Combine token functions; if any says "cannot tell", the combination does too."""

    def token(ctx: PassContext) -> str | None:
        parts: list[str] = []
        for fn in tokens:
            value = fn(ctx)
            if value is None:
                return None
            parts.append(value)
        return digest(parts)

    return token


class GatedPass:
    """Wraps a pass, skipping it while its change token is unmoved.

    ``force_every`` is the self-healing period: the Nth invocation ignores the token. Set it to
    ``0`` to disable forcing (only for a token proven exhaustive). ``enabled=False`` removes the
    gate entirely — the ablation switch the §14 measurement needs.
    """

    def __init__(
        self,
        inner: DreamingPass,
        token: ChangeToken,
        *,
        force_every: int = 20,
        enabled: bool = True,
    ) -> None:
        self._inner = inner
        self._token = token
        self._force_every = max(force_every, 0)
        self._enabled = enabled
        self._last: str | None = None
        self._since_forced = 0

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def kind(self) -> PassKind:
        return self._inner.kind

    @property
    def inner(self) -> DreamingPass:
        """The wrapped pass — for a caller that wants to run it ungated (the baseline)."""
        return self._inner

    def run(self, ctx: PassContext) -> PassOutcome:
        if not self._enabled:
            return self._inner.run(ctx)
        self._since_forced += 1
        forced = self._force_every > 0 and self._since_forced >= self._force_every
        current = self._token(ctx)
        if not forced and current is not None and current == self._last:
            return PassOutcome(
                status=PassStatus.SKIPPED,
                summary="inputs unchanged since the last run",
                details={"gated": True, "token": current},
            )
        outcome = self._inner.run(ctx)
        if forced:
            self._since_forced = 0
        if outcome.status is PassStatus.OK:
            self._last = current
        return outcome
