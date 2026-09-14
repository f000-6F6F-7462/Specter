"""FrameSource contract — holds for SyntheticFrameSource and GStreamerFrameSource alike."""

import asyncio

from specter.application.ports import FrameSource


async def test_iterates_bgr_frames_in_sequence_order(frame_source: FrameSource) -> None:
    seqs: list[int] = []
    async with asyncio.timeout(10):
        async for frame in frame_source:
            assert frame.image.ndim == 3 and frame.image.shape[2] == 3
            assert frame.stream_id == "stream_ct"
            seqs.append(frame.seq)
            if len(seqs) >= 3:
                break

    assert len(seqs) == 3
    assert seqs == sorted(seqs)
