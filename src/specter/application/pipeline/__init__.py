"""Real-time pipeline context — run a live stream into alerts and match events.

Public API: ``run_stream`` and the ``PipelineDeps`` / ``PipelineTuning`` it takes. The
``specter-ingest`` supervisor builds the deps once and calls ``run_stream`` per stream.
``run_bench``/``run_replay`` reuse the same deps against a local video file, for
``specter bench``/``specter replay``.
"""

from specter.application.pipeline.bench import BenchReport, LatencyStats, run_bench
from specter.application.pipeline.deps import PipelineDeps, PipelineTuning
from specter.application.pipeline.directory import ResolvedTarget, StreamDirectory
from specter.application.pipeline.replay import run_replay
from specter.application.pipeline.runner import StreamMetrics, StreamOutcome, run_stream

__all__ = [
    "BenchReport",
    "LatencyStats",
    "PipelineDeps",
    "PipelineTuning",
    "ResolvedTarget",
    "StreamDirectory",
    "StreamMetrics",
    "StreamOutcome",
    "run_bench",
    "run_replay",
    "run_stream",
]
