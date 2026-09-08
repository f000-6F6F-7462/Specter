"""Stream-management use cases.

Plain async functions: ``uow_factory`` first, then the request. One call == one Unit of
Work == one transaction. Cross-owner access is denied by comparing ``owner_id`` on every
read.

These only manage *configuration*. The ``specter-ingest`` supervisor watches
``desired_state`` and brings pipelines up or down to match; authoritative runtime health
travels on the ``stream_status`` event feed, not through this API.
"""

from specter.application.ports import UnitOfWork, UnitOfWorkFactory
from specter.application.streams.dto import (
    CreateStreamRequest,
    StreamView,
    UpdateStreamRequest,
)
from specter.core.errors import NotFoundError
from specter.core.ids import new_id
from specter.domain.streams import DesiredState, StreamConfig, StreamStatus

_LIVE_STATUS = {
    DesiredState.RUNNING: StreamStatus.RUNNING,
    DesiredState.STOPPED: StreamStatus.STOPPED,
}


def _view(stream: StreamConfig) -> StreamView:
    return StreamView.of(stream, live_status=_LIVE_STATUS[stream.desired_state])


async def create_stream(uow_factory: UnitOfWorkFactory, req: CreateStreamRequest) -> StreamView:
    stream = StreamConfig(
        id=new_id("stream"),
        owner_id=req.owner_id,
        name=req.name,
        source=req.source.to_domain(),
        camera_id=req.camera_id,
        watchlist_ids=list(dict.fromkeys(req.watchlist_ids)),
        sampling=req.sampling.to_domain(),
        roi=[r.to_domain() for r in req.roi],
        detect_classes=list(dict.fromkeys(req.detect_classes)),
    )
    async with uow_factory() as uow:
        await uow.streams.add(stream)
    return _view(stream)


async def list_streams(uow_factory: UnitOfWorkFactory, owner_id: str) -> list[StreamView]:
    async with uow_factory() as uow:
        streams = await uow.streams.list_for_owner(owner_id)
    return [_view(s) for s in streams]


async def get_stream(uow_factory: UnitOfWorkFactory, owner_id: str, stream_id: str) -> StreamView:
    async with uow_factory() as uow:
        stream = await _load_stream(uow, owner_id, stream_id)
    return _view(stream)


async def update_stream(
    uow_factory: UnitOfWorkFactory,
    owner_id: str,
    stream_id: str,
    req: UpdateStreamRequest,
) -> StreamView:
    async with uow_factory() as uow:
        stream = await _load_stream(uow, owner_id, stream_id)
        if req.name is not None:
            stream.rename(req.name)
        if req.source is not None:
            stream.retarget(req.source.to_domain())
        if req.camera_id is not None:
            stream.camera_id = req.camera_id
        if req.watchlist_ids is not None:
            stream.set_watchlists(list(req.watchlist_ids))
        if req.sampling is not None:
            stream.set_sampling(req.sampling.to_domain())
        if req.roi is not None:
            stream.set_roi([r.to_domain() for r in req.roi])
        if req.detect_classes is not None:
            stream.set_detect_classes(list(req.detect_classes))
        if req.enabled is True:
            stream.enable()
        elif req.enabled is False:
            stream.disable()
        await uow.streams.update(stream)
    return _view(stream)


async def set_stream_state(
    uow_factory: UnitOfWorkFactory, owner_id: str, stream_id: str, *, running: bool
) -> StreamView:
    async with uow_factory() as uow:
        stream = await _load_stream(uow, owner_id, stream_id)
        if running:
            stream.start()
        else:
            stream.stop()
        await uow.streams.update(stream)
    return _view(stream)


async def delete_stream(uow_factory: UnitOfWorkFactory, owner_id: str, stream_id: str) -> None:
    async with uow_factory() as uow:
        await _load_stream(uow, owner_id, stream_id)
        await uow.streams.delete(stream_id)


async def _load_stream(uow: UnitOfWork, owner_id: str, stream_id: str) -> StreamConfig:
    stream = await uow.streams.get(stream_id)
    if stream is None or stream.owner_id != owner_id:
        raise NotFoundError(f"stream {stream_id}")
    return stream
