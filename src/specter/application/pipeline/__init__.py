"""Real-time pipeline context — run a live stream into alerts and match events.

Public API: ``run_stream`` and the ``PipelineDeps`` / ``PipelineTuning`` it takes. The
``specter-ingest`` supervisor builds the deps once and calls ``run_stream`` per stream.
"""

from specter.application.pipeline.deps import PipelineDeps, PipelineTuning
from specter.application.pipeline.directory import ResolvedTarget, StreamDirectory
from specter.application.pipeline.runner import StreamMetrics, StreamOutcome, run_stream

__all__ = [
    "PipelineDeps",
    "PipelineTuning",
    "ResolvedTarget",
    "StreamDirectory",
    "StreamMetrics",
    "StreamOutcome",
    "run_stream",
]
