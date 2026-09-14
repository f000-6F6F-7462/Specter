"""``specter replay`` — run the real pipeline against a recorded video file instead of a
live camera, for an already-configured stream (its watchlists/sampling/roi apply
unchanged). Needs the same backing services as ``specter ingest``.
"""

import asyncio
import contextlib
import logging

from specter.application.pipeline import PipelineDeps, StreamOutcome, run_replay
from specter.core.errors import NotFoundError

log = logging.getLogger(__name__)


def main(video_path: str, stream_id: str) -> None:  # pragma: no cover - process entrypoint
    from specter.core.di import build_container  # pylint: disable=import-outside-toplevel

    async def _run() -> StreamOutcome:
        container = build_container()
        deps = PipelineDeps(
            uow_factory=container.uow_factory,
            frame_source_factory=container.frame_source_factory,
            detector=container.detector,
            tracker=container.tracker,
            embedders=container.embedders,
            vectors=container.vectors,
            blob=container.blob,
            bus=container.bus,
            health=container.health,
            clock=container.clock,
            codec=container.codec,
            tuning=container.pipeline_tuning,
        )
        async with contextlib.AsyncExitStack() as stack:
            for adapter in container.lifecycle:
                await stack.enter_async_context(adapter)
            async with container.uow_factory() as uow:
                stream = await uow.streams.get(stream_id)
            if stream is None:
                raise NotFoundError(f"stream {stream_id}")
            return await run_replay(deps, stream, video_path)

    outcome = asyncio.run(_run())
    print(f"reason:    {outcome.reason}")
    print(f"processed: {outcome.metrics.processed}")
    print(f"matches:   {outcome.metrics.matches}")
