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
