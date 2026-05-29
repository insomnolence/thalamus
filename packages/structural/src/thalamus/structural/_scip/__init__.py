"""Vendored SCIP protobuf bindings.

``scip_pb2.py`` is generated (``protoc``) from the pinned ``scip.proto`` — see
``README.md`` for the regen command and the pinned upstream commit. It is committed
so neither ``protoc`` nor a network fetch is needed at build/runtime; the only runtime
dependency is the pure-Python ``protobuf`` wheel (the ``scip`` extra), imported lazily
by :class:`~thalamus.structural.scip_ingestor.ScipIngestor`.
"""
