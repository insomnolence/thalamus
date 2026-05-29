# Vendored SCIP protobuf bindings

`scip_pb2.py` is generated from `scip.proto`, which is pinned from
[`sourcegraph/scip`](https://github.com/sourcegraph/scip) at commit
**`cb90983235d31b9862865e8352f91f6f095b2ac8`** (`scip.proto`, fetched 2026-05-29).

Both files are committed deliberately: the build/runtime needs neither `protoc` nor a
network fetch — only the pure-Python `protobuf` wheel (the `scip` optional extra).

## Regenerating

Only when intentionally bumping the pinned proto:

```bash
cd packages/structural/src/thalamus/structural/_scip
# refresh scip.proto from a chosen upstream commit, then:
protoc --python_out=. --proto_path=. scip.proto
```

`scip_pb2.py` is excluded from ruff + mypy (it is generated; do not hand-edit).
