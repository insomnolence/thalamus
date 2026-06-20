# Deep dive — Threat model & content-trust (§17)

*Part of [Project Thalamus design notes](../design-notes.md), worked through 2026-06-15. Section
numbers `§17.x` are used globally across the design notes; cross-references to §13 (outcome-learned
retrieval), §14 (design principles), §16 (roadmap) point back to those docs.*

---

## 17. Overview

Thalamus has, to date, no content-trust layer. That has been *correct* — a solo brain on the
author's own machines, ingesting the author's own repos, has no untrusted producer in the loop. This
note scopes the security workstream for when that assumption stops holding, and — more importantly —
draws the line between the security that matters for a single-operator brain and the security that
does not, so the work is a **deliberate, gated deferral** like every other frontier thread, not a
blind spot a reviewer finds first.

### 17.1 Stance

The governing axis is the same as everywhere else in this project: **measured vs. unmeasured, scoped
vs. unscoped — not "more security is better."** Two failure modes are symmetric:

- **Under-build:** ship a self-feeding brain that re-injects untrusted content into the actuator's
  prompt, or embeds a leaked secret into a vector store you can't `git rm`. This is a real exposure
  *today*, masked only by "it's just me on my own code."
- **Over-build:** import a hosted multi-tenant product's security model (RLS, per-user isolation,
  role gates, key rotation) into a single-user research tool. That is solving a problem we do not
  have, at the cost of the problem we do (the measurement loop, §13/§16).

The discriminator is the **threat model**, not a checklist. Build the slice the architecture
actually exposes; refuse the slice that only exists for multi-tenancy.

---

## 17.2 Threat model

Thalamus's value proposition *is* its exposure: it fuses experiential history and a live code/doc
graph back into the actuator's context on every recall. That makes three threats real **regardless
of user count**, because the operator is not the only author of what enters the brain.

**T1 — Untrusted-content injection (recall path).** The brain ingests third-party code, dependency
source, commit messages, error strings, docs, and arbitrary `[[corpus]]` material. Any of it can
contain instruction-shaped text (a poisoned docstring, a malicious vendored README, an `error:
ignore previous instructions and…` string). On recall it is stitched into the actuator's prompt. A
brain built to *richly inform* the actuator gives an injected payload **more** leverage than a
passive note store does — this is the inverse of the usual "memory is low-risk" intuition.

**T2 — Secret capture (ingest path).** Embedding code and trajectory logs routinely sweeps in API
keys, tokens, `.env` values, internal hostnames. Once captured they live in a vector index and a
Neo4j store — surfaced on recall, and far harder to expunge than a single file. A brain becomes a
secret-propagation surface.

**T3 — Memory poisoning (integrity of a self-updating store).** Because the brain reorganizes itself
(supersession, credibility, cross-links) and steers future behavior, a bad memory is not noise — it
is a *persistent influence* on the actuator. Supersession + credibility (§13.8) address **drift**
(beliefs that became false over time); they do **not** address **adversarial** content planted to
steer recall. Different threat, different defense.

### 17.3 Explicit non-goals

- **Multi-tenant isolation / RLS / per-user row scoping.** Thalamus is single-operator by design.
  The `(tenant_id, repo_id)` scoping that already exists in the store is for *namespacing* (don't
  let repo A's facts overwrite repo B's — §1), **not** a security boundary between distinct users.
  No RLS, no role gates, no per-user auth. If the design ever goes multi-user this section is
  reopened — until then, building it is over-build (§17.1).
- **Key rotation / audit-log admin surface / allowlist governance.** All multi-tenant-product
  concerns. Out of scope.
- **Network/transport hardening of the MCP serve beyond localhost.** The serve binds local; remote
  exposure is a separate decision that would carry its own threat model.

---

## 17.4 In-scope mechanisms (build order, gated)

Bias toward **deterministic** defenses over latent/learned ones (§4 "gotcha detection — deterministic
analysis, not latent geometry"). The threats above are largely pattern- and provenance-shaped, which
classical methods handle without an unmeasurable model in the path.

1. **Provenance tagging at capture (foundation — do first, cheap).** Every node carries
   `trust: operator | derived | third-party` already implicit in *which corpus / which producer*
   emitted it. Make it explicit and persisted. This is the seam every other defense reads; it is
   also near-free given the existing `[[corpus]]` model (a corpus declares its trust once). Mirrors
   Dollhouse's trust-level idea but keyed on *producer*, not per-entry promotion.

2. **Recall-path content fencing (T1).** When the payload renderer (`gateway/payload.py`) emits a
   node whose provenance is not `operator`, wrap it in a visible, non-instruction delimiter so the
   actuator treats it as *data about the world*, not *instructions to follow* — the same move as
   Dollhouse's untrusted-content sandboxing on display. Deterministic, no model. The renderer is
   already the single choke point for everything that reaches the actuator, so this is one
   well-placed change, not a sprinkle.

3. **Secret redaction at ingest (T2).** A deterministic scrubber on the capture path (high-entropy
   strings + known key/token shapes: AWS, GitHub, OpenAI, JWT, PEM, `.env` assignments) that
   redacts before embedding/storage. Run it at the boundary so secrets never enter the vector index
   or Neo4j in the first place — redaction-after-the-fact on an embedded store is the hard case we
   want to avoid being in. Log redaction *events* (not the secret) so coverage is auditable.

4. **Poison-resistance for the self-updating layer (T3) — gated, last.** Treat adversarial content
   as a credibility input: a memory whose provenance is `third-party` cannot, on its own, reach a
   credibility tier high enough to re-rank above operator-authored memory. This composes with the
   existing fate/credibility machinery rather than adding a parallel system — but it is **gated
   behind credibility step B/C** (§13.8 roadmap): there is no point hardening a re-ranking signal
   that is not yet energized. Until C ships, T3 is covered conservatively by (1)+(2): untrusted
   content is *fenced and visible*, even if not yet *down-weighted*.

---

## 17.5 Gating — relative to the measurement loop

**This workstream does NOT jump the queue ahead of credibility step B** (close the measurement loop,
un-blind the proxy↔truth monitor — §13.8, STATUS "Next steps") **unless the threat model changes
first.** Security makes the brain *trustworthy and deployable*; it does not advance the *core thesis*
(does the brain make the actuator measurably better) by one inch. The thesis is the lever; this is
table-stakes for pointing the brain at content we don't control.

Concrete trigger that *does* promote this ahead of step B:

- Ingesting a corpus the operator does not author/control (a third-party dependency tree as a
  Brain-2 corpus, an external analysis producer's output — §16 step 4), **or**
- Sharing/exposing the brain beyond the single local operator in any form.

Until one of those is true, steps (1)–(3) are **low-cost, do-anytime** hardening (provenance is
near-free and unblocks the rest; fencing and redaction are bounded deterministic passes at the two
choke points). Step (4) stays **deferred behind credibility C**, documented here so it's a sequenced
decision, not an omission.

### 17.6 Honest framing

The security gap is the *easier, more bounded* problem — two choke points (ingest, render), mostly
deterministic, no learned component in the path. The hard problem (does self-updating memory make the
actuator better, measured) is already the one this project is solving. Closing the security slice
removes the one axis on which a passive multi-tenant note store legitimately leads a single-user
brain; it does not change the fact that the brain is already in the better space on the dimension
that defines a memory system. Build it when the threat model earns it — and write the trigger down
(§17.5) so "later" stays a decision rather than drift.
