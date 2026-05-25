"""Thalamus exception hierarchy.

All cross-package errors inherit from :class:`ThalamusError` and carry structured
attributes so callers can inspect failures programmatically rather than parsing
message strings. (Pattern referenced from Polynoica's ``core/exceptions.py``;
the JEPA/hypersphere-specific errors are intentionally not carried over.)
"""

from __future__ import annotations


class ThalamusError(Exception):
    """Base class for all Thalamus errors."""


class ConfigurationError(ThalamusError):
    """A component was configured with invalid or missing settings."""


class StoreError(ThalamusError):
    """A memory-store operation failed."""


class EncoderError(ThalamusError):
    """Text-to-vector encoding failed."""


class DimensionMismatchError(StoreError):
    """A vector's dimensionality does not match the index it is used with.

    Attributes:
        expected: Dimensionality the index requires.
        actual: Dimensionality that was provided.
        context: Free-form description of where the mismatch occurred.
    """

    def __init__(self, expected: int, actual: int, context: str = "") -> None:
        self.expected = expected
        self.actual = actual
        self.context = context
        message = f"Vector dimension mismatch: expected {expected}, got {actual}"
        if context:
            message = f"{message} ({context})"
        super().__init__(message)
