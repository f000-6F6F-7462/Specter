"""``specter`` CLI dispatch — each subcommand calls the matching process entrypoint."""

import pytest

from specter.entrypoints import cli


@pytest.mark.parametrize(
    ("command", "target"),
    [
        ("api", "specter.entrypoints.http.asgi.main"),
        ("ingest", "specter.entrypoints.workers.ingest_worker.main"),
        ("enroll", "specter.entrypoints.workers.enroll_worker.main"),
    ],
)
def test_dispatches_to_the_matching_entrypoint(
    monkeypatch: pytest.MonkeyPatch, command: str, target: str
) -> None:
    calls: list[str] = []
    module_path, attr = target.rsplit(".", 1)
    monkeypatch.setattr(f"{module_path}.{attr}", lambda: calls.append(command))

    cli.main([command])

    assert calls == [command]


def test_missing_command_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])
    assert exc_info.value.code != 0


def test_unknown_command_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["bogus"])
    assert exc_info.value.code != 0


def test_bench_dispatches_with_video_and_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        "specter.entrypoints.workers.bench.main",
        lambda video, *, frames=None: calls.append((video, frames)),
    )

    cli.main(["bench", "clip.mp4", "--frames", "50"])

    assert calls == [("clip.mp4", 50)]


def test_bench_frames_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        "specter.entrypoints.workers.bench.main",
        lambda video, *, frames=None: calls.append((video, frames)),
    )

    cli.main(["bench", "clip.mp4"])

    assert calls == [("clip.mp4", None)]


def test_replay_dispatches_with_video_and_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        "specter.entrypoints.workers.replay.main",
        lambda video, stream_id: calls.append((video, stream_id)),
    )

    cli.main(["replay", "clip.mp4", "--stream", "stream_1"])

    assert calls == [("clip.mp4", "stream_1")]


def test_replay_requires_stream_argument() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["replay", "clip.mp4"])
    assert exc_info.value.code != 0
