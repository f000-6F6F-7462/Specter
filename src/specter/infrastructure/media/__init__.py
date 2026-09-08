"""Media capture adapters — decoded video frames off a live connection.

``SyntheticFrameSource`` needs no backing media and drives the test + local-dev
pipeline.
"""

from specter.infrastructure.media.fakes import SyntheticFrameSource

__all__ = ["SyntheticFrameSource"]
