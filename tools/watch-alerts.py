"""Prints Specter's alerts as they happen, the way an application server receives them.

This is the push channel: it subscribes to NATS and prints each alert the moment Specter
publishes it, rather than asking the HTTP API over and over. An application server would do
exactly this, except that it would use a durable JetStream consumer so that events raised while
it was down are delivered when it comes back.

Read-only: it subscribes and prints, and never publishes or changes anything.

Run it from the repository root:

    uv run python tools/watch-alerts.py            # owner "demo"
    uv run python tools/watch-alerts.py acme       # another owner
"""

import asyncio
import json
import sys
from datetime import UTC, datetime

import nats

SERVER = "nats://127.0.0.1:4222"


def local_time(moment: datetime) -> str:
    """Returns the moment in this machine's timezone, since Specter publishes UTC."""
    return moment.astimezone().strftime("%H:%M:%S")


async def main() -> None:
    """Subscribes to one owner's events and prints them until interrupted."""
    owner_id = sys.argv[1] if len(sys.argv) > 1 else "demo"
    subject = f"specter.owners.{owner_id}.>"

    async def on_error(error: Exception) -> None:
        print(f"!! NATS error: {error}", flush=True)

    async def on_disconnect() -> None:
        print("!! NATS disconnected, waiting to reconnect", flush=True)

    connection = await nats.connect(
        SERVER, error_cb=on_error, disconnected_cb=on_disconnect, max_reconnect_attempts=-1
    )

    async def on_message(message: object) -> None:
        body = json.loads(message.data)  # type: ignore[attr-defined]
        event = message.subject.rsplit(".", 1)[-1]  # type: ignore[attr-defined]
        received = local_time(datetime.now(UTC))
        if event == "match_confirmed":
            print(
                f"{received}  MATCH      target={body['target_id']}  "
                f"similarity={body['similarity_ratio']:.3f}  "
                f"threshold={body['threshold_ratio']}  camera={body['camera_id']}\n"
                f"           alert id={body['message_id']}",
                flush=True,
            )
        elif event == "rule_triggered":
            print(f"{received}  RULE       rule={body.get('rule_id')}", flush=True)
        elif event == "status_changed":
            print(f"{received}  STATUS     {body.get('status')}", flush=True)
        else:
            print(f"{received}  {event}", flush=True)

    await connection.subscribe(subject, cb=on_message)
    print(f"listening on {subject} — Ctrl+C to stop", flush=True)
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped")
