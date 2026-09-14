"""Media capture adapters — decoded video frames off a live connection.

``GStreamerFrameSource`` is the primary source (``[media]`` extra + system GStreamer
plugins). ``SyntheticFrameSource`` needs no backing media and drives the test +
local-dev pipeline. ``NumpyFrameCodec`` serialises match evidence without an image
library.
"""

from specter.infrastructure.media.codec import NumpyFrameCodec
from specter.infrastructure.media.fakes import SyntheticFrameSource
from specter.infrastructure.media.gstreamer import (
    GStreamerFrameSource,
    build_pipeline_description,
    redacted_description,
)

__all__ = [
    "GStreamerFrameSource",
    "NumpyFrameCodec",
    "SyntheticFrameSource",
    "build_pipeline_description",
    "redacted_description",
]
