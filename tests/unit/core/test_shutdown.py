import asyncio
import os
import signal
from functools import partial

import pytest

from specter.core.shutdown import (
    complete_unless_shutdown,
    install_shutdown_signal_handlers,
    run_until_shutdown_signal,
)

SIGNAL_DELIVERY_TIMEOUT_SECONDS = 1.0
NEVER_FINISHING_SECONDS = 3600.0


async def record_shutdown_event(
    shutdown_requested: asyncio.Event, *, received_events: list[asyncio.Event]
) -> None:
    received_events.append(shutdown_requested)


@pytest.mark.parametrize("shutdown_signal", [signal.SIGINT, signal.SIGTERM])
async def test_shutdown_is_requested_when_process_receives_a_stop_signal(
    shutdown_signal: signal.Signals,
) -> None:
    shutdown_requested = asyncio.Event()
    install_shutdown_signal_handlers(shutdown_requested)

    os.kill(os.getpid(), shutdown_signal)

    await asyncio.wait_for(shutdown_requested.wait(), SIGNAL_DELIVERY_TIMEOUT_SECONDS)


async def test_process_receives_an_unset_shutdown_event_when_started() -> None:
    received_events: list[asyncio.Event] = []

    await run_until_shutdown_signal(partial(record_shutdown_event, received_events=received_events))

    assert len(received_events) == 1
    assert not received_events[0].is_set()


async def test_operation_is_completed_when_it_finishes_before_shutdown() -> None:
    shutdown_requested = asyncio.Event()

    is_completed = await complete_unless_shutdown(asyncio.sleep(0), shutdown_requested)

    assert is_completed


async def test_operation_is_cancelled_when_shutdown_is_requested_first() -> None:
    shutdown_requested = asyncio.Event()
    operation_task = asyncio.create_task(asyncio.sleep(NEVER_FINISHING_SECONDS))
    shutdown_requested.set()

    is_completed = await complete_unless_shutdown(operation_task, shutdown_requested)

    assert not is_completed
    assert operation_task.cancelled()


async def test_gathered_operations_are_cancelled_when_shutdown_is_requested_first() -> None:
    shutdown_requested = asyncio.Event()
    shutdown_requested.set()

    is_completed = await complete_unless_shutdown(
        asyncio.gather(asyncio.sleep(NEVER_FINISHING_SECONDS)), shutdown_requested
    )

    assert not is_completed
