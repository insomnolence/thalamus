"""thalamus.routing — embedding encoder + intent classifier.

Currently provides the text encoders (the intent classifier lands when the
routing layer is built out). All encoders satisfy :class:`thalamus.core.Encoder`;
:func:`build_encoder` is the seam that selects one by name.
"""

from thalamus.routing.encoders import (
    ENCODER_NAMES,
    BgeEncoder,
    DeterministicEncoder,
    FastEmbedEncoder,
    build_encoder,
    default_model_cache_dir,
)

__all__ = [
    "BgeEncoder",
    "DeterministicEncoder",
    "ENCODER_NAMES",
    "FastEmbedEncoder",
    "build_encoder",
    "default_model_cache_dir",
]
