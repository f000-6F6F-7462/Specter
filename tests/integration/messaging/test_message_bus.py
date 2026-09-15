import asyncio

import nats
import pytest
from pydantic import BaseModel

from specter.messaging.client import MessageBus
from specter.messaging.streams import CAMERA_STATUS_STREAM
from specter.messaging.subjects import CameraEvent, build_camera_subject

pytestmark = pytest.mark.integration

DELIVERY_TIMEOUT_SECONDS = 5.0
OWNER_ID = "owner_tests"


class CameraStatusMessage(BaseModel):
    camera_id: str
    status: str


async def test_declaring_streams_succeeds_when_repeated(message_bus: MessageBus) -> None:
    await message_bus.declare_streams()
    await message_bus.declare_streams()


async def test_published_message_is_stored_in_its_stream_when_streams_are_declared(
    message_bus: MessageBus, nats_server_url: str, unique_camera_id: str
) -> None:
    await message_bus.declare_streams()
    subject = build_camera_subject(OWNER_ID, unique_camera_id, CameraEvent.STATUS_CHANGED)

    await message_bus.publish(
        subject, CameraStatusMessage(camera_id=unique_camera_id, status="running")
    )

    inspection_connection = await nats.connect(nats_server_url)
    try:
        stream_info = await inspection_connection.jetstream().stream_info(
            CAMERA_STATUS_STREAM.name, subjects_filter=subject
        )
    finally:
        await inspection_connection.close()
    assert stream_info.state.subjects == {subject: 1}


async def test_subscriber_receives_parsed_message_when_one_is_published(
    message_bus: MessageBus, unique_camera_id: str
) -> None:
    await message_bus.declare_streams()
    subject = build_camera_subject(OWNER_ID, unique_camera_id, CameraEvent.STATUS_CHANGED)
    received_messages: asyncio.Queue[CameraStatusMessage] = asyncio.Queue()
    await message_bus.subscribe(subject, CameraStatusMessage, received_messages.put)

    published_message = CameraStatusMessage(camera_id=unique_camera_id, status="running")
    await message_bus.publish(subject, published_message)

    received_message = await asyncio.wait_for(received_messages.get(), DELIVERY_TIMEOUT_SECONDS)
    assert received_message == published_message
