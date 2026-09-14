"""``specter bench`` — raw detect/embed throughput over a local video file.

No backing services needed beyond whatever the configured detector/embedder
themselves require (model weights) — no Postgres/Redis/Qdrant/MinIO.
"""

import asyncio
import contextlib
import logging

from specter.application.pipeline import BenchReport, LatencyStats, run_bench
from specter.domain.streams import StreamProtocol, StreamSource

log = logging.getLogger(__name__)


def main(
    video_path: str, *, frames: int | None = None
) -> None:  # pragma: no cover - process entrypoint
    from specter.core.di import build_container  # pylint: disable=import-outside-toplevel
    from specter.infrastructure.media.pyav import (  # pylint: disable=import-outside-toplevel
        PyAvFrameSource,
    )

    async def _run() -> BenchReport:
        container = build_container()
        async with contextlib.AsyncExitStack() as stack:
            for adapter in container.lifecycle:
                await stack.enter_async_context(adapter)
            source = PyAvFrameSource(
                "bench",
                StreamSource(protocol=StreamProtocol.HLS, url=video_path),
                drop_when_full=False,
            )
            embedder = next(iter(container.embedders.values()), None)
            try:
                return await run_bench(source, container.detector, embedder, max_frames=frames)
            finally:
                await source.aclose()

    report = asyncio.run(_run())
    _print(report)


def _print(report: BenchReport) -> None:
    print(f"frames:  {report.frames}")
    print(f"wall:    {report.wall_s:.2f}s ({report.fps:.2f} fps)")
    _print_stage("detect", report.detect)
    _print_stage("embed", report.embed)


def _print_stage(name: str, stats: LatencyStats | None) -> None:
    if stats is None:
        print(f"{name}:  (no samples)")
        return
    print(
        f"{name}:  n={stats.count} mean={stats.mean_ms:.1f}ms "
        f"p50={stats.p50_ms:.1f}ms p95={stats.p95_ms:.1f}ms max={stats.max_ms:.1f}ms"
    )
