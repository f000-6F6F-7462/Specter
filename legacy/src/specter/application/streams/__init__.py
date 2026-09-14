"""Streams context — CRUD for live-stream configuration and its run intent.

Public API: the use-case functions and the DTOs re-exported below. Callers pass the
dependencies first, then the request.
"""

from specter.application.streams.dto import (
    CreateStreamRequest,
    RoiSpec,
    RoiView,
    SamplingSpec,
    SamplingView,
    SourceSpec,
    StreamView,
    UpdateStreamRequest,
)
from specter.application.streams.use_cases import (
    create_stream,
    delete_stream,
    get_stream,
    list_streams,
    set_stream_state,
    update_stream,
)

__all__ = [
    "CreateStreamRequest",
    "RoiSpec",
    "RoiView",
    "SamplingSpec",
    "SamplingView",
    "SourceSpec",
    "StreamView",
    "UpdateStreamRequest",
    "create_stream",
    "delete_stream",
    "get_stream",
    "list_streams",
    "set_stream_state",
    "update_stream",
]
