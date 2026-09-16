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


async def complete_unless_shutdown(
    operation: Awaitable[object], shutdown_requested: asyncio.Event
) -> bool:
    """Waits for the operation and returns True, or cancels it and returns False on shutdown.

    Lets a process stop while it still waits for something that may never finish, such as a
    service that is not reachable yet.
    """
    operation_task = asyncio.ensure_future(operation)
    shutdown_task = asyncio.ensure_future(shutdown_requested.wait())
    try:
        await asyncio.wait((operation_task, shutdown_task), return_when=asyncio.FIRST_COMPLETED)
    finally:
        operation_task.cancel()
        shutdown_task.cancel()
        await asyncio.gather(operation_task, shutdown_task, return_exceptions=True)
    if operation_task.cancelled():
        return False
    operation_task.result()
    return True


async def run_until_shutdown_signal(process: Callable[[asyncio.Event], Awaitable[None]]) -> None:
    """Runs the process with a shutdown event that is set on SIGINT or SIGTERM."""
    shutdown_requested = asyncio.Event()
    install_shutdown_signal_handlers(shutdown_requested)
    await process(shutdown_requested)
