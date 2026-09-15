"""Objects that request handlers receive through FastAPI dependency injection."""

from typing import Annotated

from fastapi import Depends, Request

from specter.messaging.client import MessageBus


def get_message_bus(request: Request) -> MessageBus:
    """Returns the message bus opened by the application's lifespan."""
    message_bus: MessageBus = request.app.state.message_bus
    return message_bus


MessageBusDependency = Annotated[MessageBus, Depends(get_message_bus)]
