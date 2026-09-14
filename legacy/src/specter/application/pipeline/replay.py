"""``specter replay`` — run the real pipeline (detect, track, embed, match, emit) against
a recorded video file instead of a live camera, by swapping in a lossless
``PyAvFrameSource`` pointed at the file for one ``run_stream`` call.

The stream's own config (watchlists, sampling, roi, ...) still comes from the DB — only
the frame source changes — so a replay exercises exactly the same matching behaviour a
live run of that stream would.
"""

import asyncio
from dataclasses import replace

from specter.application.pipeline.deps import PipelineDeps
from specter.application.pipeline.runner import StreamOutcome, run_stream
from specter.domain.streams import StreamConfig


async def run_replay(deps: PipelineDeps, stream: StreamConfig, video_path: str) -> StreamOutcome:
    from specter.infrastructure.media.pyav import (
        PyAvFrameSource,  # pylint: disable=import-outside-toplevel
    )

    file_stream = replace(stream, source=replace(stream.source, url=video_path))

    def file_source(cfg: StreamConfig) -> PyAvFrameSource:
        return PyAvFrameSource(cfg.id, cfg.source, drop_when_full=False)

    replay_deps = replace(deps, frame_source_factory=file_source)
    return await run_stream(replay_deps, file_stream, stop=asyncio.Event())
