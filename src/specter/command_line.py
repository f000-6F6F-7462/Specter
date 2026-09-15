"""The ``specter`` command, which starts one of the device processes."""

import argparse
import asyncio
from collections.abc import Sequence
from functools import partial
from pathlib import Path

from pydantic import ValidationError

from specter.config.loading import CONFIG_FILE_ENVIRONMENT_VARIABLE, load_settings
from specter.core.errors import ConfigurationError
from specter.core.logging import configure_logging
from specter.core.shutdown import run_until_shutdown_signal

CONFIGURATION_ERROR_EXIT_CODE = 2


def build_argument_parser() -> argparse.ArgumentParser:
    """Returns the parser for the ``specter`` command and its subcommands."""
    parser = argparse.ArgumentParser(
        prog="specter", description="Real-time multi-camera edge vision engine."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"static settings YAML file (default: ${CONFIG_FILE_ENVIRONMENT_VARIABLE})",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("api", help="serve the local HTTP API")
    commands.add_parser("camera-manager", help="start and supervise one process per camera")
    camera_parser = commands.add_parser("camera", help="capture and analyze a single camera")
    camera_parser.add_argument("--camera-id", required=True, help="id of the camera to run")
    commands.add_parser("detector", help="serve models to all camera processes")
    commands.add_parser("migrate", help="apply pending database migrations")
    return parser


def main(arguments: Sequence[str] | None = None) -> None:
    """Parses the command line and runs the selected command until it finishes."""
    parser = build_argument_parser()
    parsed_arguments = parser.parse_args(arguments)
    try:
        settings = load_settings(parsed_arguments.config)
    except (ConfigurationError, ValidationError) as error:
        parser.exit(CONFIGURATION_ERROR_EXIT_CODE, f"specter: invalid settings: {error}\n")
    configure_logging(settings.logging.level, settings.logging.format)

    # Each process imports only its own modules, so a camera process never loads FastAPI.
    match parsed_arguments.command:
        case "api":
            from specter.api.main import run as run_api  # noqa: PLC0415

            run_api(settings)
        case "camera-manager":
            from specter.camera_manager.main import run as run_camera_manager  # noqa: PLC0415

            asyncio.run(run_until_shutdown_signal(partial(run_camera_manager, settings)))
        case "camera":
            from specter.camera.main import run as run_camera  # noqa: PLC0415

            asyncio.run(
                run_until_shutdown_signal(partial(run_camera, settings, parsed_arguments.camera_id))
            )
        case "detector":
            from specter.detector.main import run as run_detector  # noqa: PLC0415

            asyncio.run(run_until_shutdown_signal(partial(run_detector, settings)))
        case "migrate":
            from specter.storage.migrate import migrate_database_file  # noqa: PLC0415

            migrate_database_file(settings.paths.database_file)
