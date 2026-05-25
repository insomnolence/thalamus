"""thalamus.routing — embedding encoder + intent classifier.

Currently provides the text encoders (the intent classifier lands when the
routing layer is built out). Both encoders satisfy :class:`thalamus.core.Encoder`.
"""

from thalamus.routing.encoders import BgeEncoder, DeterministicEncoder

__all__ = ["BgeEncoder", "DeterministicEncoder"]
