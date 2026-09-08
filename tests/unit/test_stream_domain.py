import pytest

from specter.core.errors import RuleViolation
from specter.domain.streams import (
    DesiredState,
    SamplingConfig,
    StreamConfig,
    StreamProtocol,
    StreamSource,
)


def _stream(**kw: object) -> StreamConfig:
    defaults: dict[str, object] = {
        "id": "stream_1",
        "owner_id": "o_1",
        "name": "Front door",
        "source": StreamSource(protocol=StreamProtocol.RTSP, url="rtsp://cam/1"),
    }
    defaults.update(kw)
    return StreamConfig(**defaults)  # type: ignore[arg-type]


def test_rename_rejects_blank() -> None:
    stream = _stream()
    stream.rename("Loading bay")
    assert stream.name == "Loading bay"
    with pytest.raises(RuleViolation):
        stream.rename("   ")


def test_start_requires_enabled() -> None:
    stream = _stream(enabled=False)
    with pytest.raises(RuleViolation):
        stream.start()

    stream.enable()
    stream.start()
    assert stream.desired_state.value == "running"
    assert stream.should_run

    stream.stop()
    assert stream.desired_state.value == "stopped"
    assert not stream.should_run


def test_disable_parks_desired_state() -> None:
    stream = _stream()
    stream.start()
    stream.disable()
    assert not stream.enabled
    assert stream.desired_state is DesiredState.STOPPED


def test_set_watchlists_dedupes_preserving_order() -> None:
    stream = _stream()
    stream.set_watchlists(["wl_b", "wl_a", "wl_b", "wl_c"])
    assert stream.watchlist_ids == ["wl_b", "wl_a", "wl_c"]


def test_set_sampling_swaps_config() -> None:
    stream = _stream()
    stream.set_sampling(SamplingConfig(target_fps=6.0, min_fps=2.0))
    assert stream.sampling.target_fps == 6.0
