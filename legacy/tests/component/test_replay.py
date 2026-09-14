"""``run_replay`` end-to-end: same harness as test_stream_pipeline.py, but frames come
from a real local video file (via PyAvFrameSource) instead of SyntheticFrameSource."""

from pathlib import Path

import numpy as np

from specter.application.pipeline import run_replay
from tests.component.test_stream_pipeline import PipelineHarness, harness  # noqa: F401


def _write_test_video(path: Path, *, frames: int, size: tuple[int, int] = (64, 64)) -> None:
    import av

    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=10)
    stream.width, stream.height = size
    stream.pix_fmt = "yuv420p"
    for i in range(frames):
        arr = np.full((size[1], size[0], 3), (i * 20) % 256, dtype=np.uint8)
        vframe = av.VideoFrame.from_ndarray(arr, format="rgb24")
        for packet in stream.encode(vframe):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


async def test_replay_fires_a_match_from_a_recorded_file(
    harness: PipelineHarness, tmp_path: Path
) -> None:
    video = tmp_path / "recorded.mp4"
    _write_test_video(video, frames=6)

    outcome = await run_replay(harness.deps, harness.stream, str(video))

    assert outcome.reason == "source_exhausted"
    # Every frame reaches the pipeline (PyAvFrameSource's own queue never drops in
    # replay mode) — but the stream's AdaptiveSampler still rate-gates against real
    # elapsed time same as it would live, and a small local file decodes far faster
    # than target_fps implies, so it can legitimately shed a frame or two here.
    assert outcome.metrics.received == 6
    assert outcome.metrics.matches == 1

    events = harness.match_events()
    assert len(events) == 1
    assert events[0].match.target_id == "tgt_1"

    async with harness.uow_factory() as uow:
        alerts = await uow.alerts.list_for_owner("o_1")
    assert len(alerts) == 1


async def test_replay_does_not_mutate_the_stored_stream_config(
    harness: PipelineHarness, tmp_path: Path
) -> None:
    video = tmp_path / "recorded.mp4"
    _write_test_video(video, frames=2)
    original_url = harness.stream.source.url

    await run_replay(harness.deps, harness.stream, str(video))

    assert harness.stream.source.url == original_url
