from pathlib import Path

import pytest

from specter.command_line import build_argument_parser


def test_parser_exits_when_no_command_is_given() -> None:
    with pytest.raises(SystemExit):
        build_argument_parser().parse_args([])


def test_parser_exits_when_camera_command_has_no_camera_id() -> None:
    with pytest.raises(SystemExit):
        build_argument_parser().parse_args(["camera"])


def test_parser_reads_camera_id_when_camera_command_is_given() -> None:
    parsed_arguments = build_argument_parser().parse_args(["camera", "--camera-id", "front_door"])

    assert parsed_arguments.command == "camera"
    assert parsed_arguments.camera_id == "front_door"


def test_parser_reads_config_file_as_path_when_given() -> None:
    parsed_arguments = build_argument_parser().parse_args(["--config", "specter.yaml", "detector"])

    assert parsed_arguments.config == Path("specter.yaml")
