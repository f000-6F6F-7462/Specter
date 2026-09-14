"""``specter`` — umbrella entrypoint dispatching to the api/ingest/enroll processes.

Thin ``argparse`` wrapper (no CLI framework dependency, matching the project's
no-framework composition style) around the standalone ``specter-api`` /
``specter-ingest`` / ``specter-enroll`` scripts, so operators can remember one
command.
"""

import argparse
import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:  # pragma: no cover - process entrypoint
    parser = argparse.ArgumentParser(prog="specter", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("api", help="run the FastAPI HTTP server")
    subparsers.add_parser("ingest", help="run the stream ingest supervisor")
    subparsers.add_parser("enroll", help="run the enrollment worker")

    bench_parser = subparsers.add_parser(
        "bench", help="raw detect/embed throughput over a local video file"
    )
    bench_parser.add_argument("video", help="path to a local video file")
    bench_parser.add_argument(
        "--frames", type=int, default=None, help="stop after this many frames"
    )

    replay_parser = subparsers.add_parser(
        "replay", help="run the real pipeline against a recorded video file"
    )
    replay_parser.add_argument("video", help="path to a local video file")
    replay_parser.add_argument(
        "--stream", required=True, help="id of an existing stream to replay against"
    )

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.command == "api":
        from specter.entrypoints.http.asgi import main as run_api

        run_api()
    elif args.command == "ingest":
        from specter.entrypoints.workers.ingest_worker import main as run_ingest

        run_ingest()
    elif args.command == "enroll":
        from specter.entrypoints.workers.enroll_worker import main as run_enroll

        run_enroll()
    elif args.command == "bench":
        from specter.entrypoints.workers.bench import main as run_bench

        run_bench(args.video, frames=args.frames)
    elif args.command == "replay":
        from specter.entrypoints.workers.replay import main as run_replay

        run_replay(args.video, args.stream)
