import asyncio

from specter.core.shutdown import complete_unless_shutdown
from specter.messaging.client import MessageBus

# Nothing listens on port 1, so every connection attempt is refused at once.
UNREACHABLE_NATS_URL = "nats://127.0.0.1:1"
SHUTDOWN_DELAY_SECONDS = 0.3
STOP_TIMEOUT_SECONDS = 5.0


async def test_connecting_stops_when_shutdown_is_requested_while_nats_is_unreachable() -> None:
    message_bus = MessageBus(client_name="specter-tests")
    shutdown_requested = asyncio.Event()
    asyncio.get_running_loop().call_later(SHUTDOWN_DELAY_SECONDS, shutdown_requested.set)

    is_connected = await asyncio.wait_for(
        complete_unless_shutdown(message_bus.connect(UNREACHABLE_NATS_URL), shutdown_requested),
        STOP_TIMEOUT_SECONDS,
    )
    await message_bus.close()

    assert not is_connected
    assert not message_bus.is_connected
