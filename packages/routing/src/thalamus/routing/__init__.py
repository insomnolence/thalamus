"""thalamus.routing — embedding encoder + intent classifier.

Currently provides the text encoders (the intent classifier lands when the
routing layer is built out). All encoders satisfy :class:`thalamus.core.Encoder`;
:func:`build_encoder` is the seam that selects one by name.
"""

from thalamus.routing.encoders import (
    BgeEncoder,
    DeterministicEncoder,
    FastEmbedEncoder,
    build_encoder,
)

__all__ = ["BgeEncoder", "DeterministicEncoder", "FastEmbedEncoder", "build_encoder"]
