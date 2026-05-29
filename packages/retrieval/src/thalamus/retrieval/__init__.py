"""thalamus.retrieval — the swappable Retriever seam.

Provides the L0 baseline now; gated rungs (bandit reweighting, bent geometry)
land as later implementations behind the same ``core.Retriever`` protocol.
"""

from thalamus.retrieval.l0 import L0Retriever
from thalamus.retrieval.recent import render_recent, select_recent

__all__ = ["L0Retriever", "render_recent", "select_recent"]
