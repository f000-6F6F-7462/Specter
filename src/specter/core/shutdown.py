"""Turns operating-system stop signals into a shutdown request that processes can watch."""

import asyncio
import signal
from collections.abc import Awaitable, Callable

SHUTDOWN_SIGNALS = (signal.SIGINT, signal.SIGTERM)


def install_shutdown_signal_handlers(shutdown_requested: asyncio.Event) -> None:
    """Sets the event when the process receives SIGINT or SIGTERM.

    Must be called from inside the running event loop.
    """
    event_loop = asyncio.get_running_loop()
    for shutdown_signal in SHUTDOWN_SIGNALS:
        event_loop.add_signal_handler(shutdown_signal, shutdown_requested.set)


async def run_until_shutdown_signal(process: Callable[[asyncio.Event], Awaitable[None]]) -> None:
    """Runs the process with a shutdown event that is set on SIGINT or SIGTERM."""
    shutdown_requested = asyncio.Event()
    install_shutdown_signal_handlers(shutdown_requested)
    await process(shutdown_requested)
