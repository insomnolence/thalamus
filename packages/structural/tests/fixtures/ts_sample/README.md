# `ts_sample` — SCIP ingestor test fixture

A tiny, dependency-free TypeScript project + its committed `index.scip`, used by the
deterministic `ScipIngestor` tests (so CI needs neither Node nor the indexer).

It deliberately exercises every mapping the ingestor performs:

- `shapes.ts` — an `interface Shape` and an `enum Kind`.
- `geometry.ts` — a top-level `function circleArea` (a cross-file call target).
- `circle.ts` — a `class Circle implements Shape` (→ an `implements` relationship); its
  `area()` method calls `circleArea` cross-file (→ a `calls` edge resolved via the
  enclosing range), plus a `constructor`.
- `index.ts` — `makeCircle` calls the `Circle` constructor cross-file.

## Regenerating `index.scip`

The index is a pinned binary fixture. Regenerate only when intentionally updating the
expectations (a `scip-typescript` upgrade, or a change to the sample sources):

```bash
npm install -g @sourcegraph/scip-typescript        # provides `scip-typescript`
cd packages/structural/tests/fixtures/ts_sample
scip-typescript index --output index.scip
```

The live-gated test (`test_scip_live.py`, skipped unless `scip-typescript` is on PATH)
regenerates into a temp dir and asserts the committed fixture still matches — a guard
against silent indexer drift.

## Inspecting it

No standalone `scip` CLI is needed; decode with `protoc` against the vendored proto:

```bash
PROTO=packages/structural/src/thalamus/structural/_scip/scip.proto
protoc --decode=scip.Index "$PROTO" --proto_path="$(dirname "$PROTO")" < index.scip
```

Generated with `scip-typescript` 0.4.0 (bundled TypeScript 5.9.3). NB: `Metadata.project_root`
is a machine-absolute `file://` URI captured at generation time — the ingestor derives all
paths from the caller-provided corpus root, not from `project_root`, so the fixture is
position-independent at test time.
