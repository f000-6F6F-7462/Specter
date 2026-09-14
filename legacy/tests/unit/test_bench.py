from specter.application.pipeline.bench import LatencyStats, run_bench
from specter.infrastructure.media.fakes import SyntheticFrameSource
from specter.infrastructure.ml.detector import FakeDetector, center_box, nothing
from specter.infrastructure.ml.embedder import FakeEmbedder


class TestLatencyStats:
    def test_empty_samples_is_none(self) -> None:
        assert LatencyStats.of([]) is None

    def test_single_sample(self) -> None:
        stats = LatencyStats.of([10.0])
        assert stats is not None
        assert stats.count == 1
        assert stats.mean_ms == stats.p50_ms == stats.p95_ms == stats.max_ms == 10.0

    def test_percentiles_are_ordered(self) -> None:
        stats = LatencyStats.of([1.0, 100.0, 2.0, 3.0, 4.0])
        assert stats is not None
        assert stats.max_ms == 100.0
        assert stats.p95_ms >= stats.p50_ms


async def test_bench_counts_frames_and_records_detect_latency() -> None:
    frames = SyntheticFrameSource("bench", count=5, fps=None)
    report = await run_bench(frames, FakeDetector(center_box()), embedder=None)

    assert report.frames == 5
    assert report.detect is not None
    assert report.detect.count == 5
    assert report.embed is None  # no embedder given


async def test_bench_records_embed_latency_only_when_detections_exist() -> None:
    frames = SyntheticFrameSource("bench", count=4, fps=None)
    report = await run_bench(frames, FakeDetector(center_box()), FakeEmbedder("face"))

    assert report.embed is not None
    assert report.embed.count == 4  # one detection per frame -> one embed call per frame


async def test_bench_skips_embed_when_nothing_detected() -> None:
    frames = SyntheticFrameSource("bench", count=4, fps=None)
    report = await run_bench(frames, FakeDetector(nothing()), FakeEmbedder("face"))

    assert report.embed is None


async def test_bench_respects_max_frames() -> None:
    frames = SyntheticFrameSource("bench", count=100, fps=None)
    report = await run_bench(frames, FakeDetector(center_box()), embedder=None, max_frames=7)

    assert report.frames == 7
