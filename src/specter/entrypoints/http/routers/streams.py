from fastapi import APIRouter, status

from specter.application import streams
from specter.entrypoints.http.deps import HealthDep, OwnerDep, UowDep
from specter.entrypoints.http.schemas import (
    StreamCreate,
    StreamOut,
    StreamStateIn,
    StreamUpdate,
)

router = APIRouter(tags=["streams"])


def _source(body: StreamCreate | StreamUpdate) -> streams.SourceSpec | None:
    if body.source is None:
        return None
    return streams.SourceSpec(
        protocol=body.source.protocol,
        url=body.source.url,
        username=body.source.username,
        password=body.source.password,
        transport=body.source.transport,
    )


def _sampling(body: StreamCreate | StreamUpdate) -> streams.SamplingSpec | None:
    if body.sampling is None:
        return None
    return streams.SamplingSpec(
        mode=body.sampling.mode,
        target_fps=body.sampling.target_fps,
        min_fps=body.sampling.min_fps,
        motion_gating=body.sampling.motion_gating,
    )


def _roi(body: StreamCreate | StreamUpdate) -> tuple[streams.RoiSpec, ...] | None:
    if body.roi is None:
        return None
    return tuple(streams.RoiSpec(x=r.x, y=r.y, w=r.w, h=r.h) for r in body.roi)


@router.post("/streams", status_code=status.HTTP_201_CREATED)
async def create_stream(
    body: StreamCreate, owner: OwnerDep, uow: UowDep, health: HealthDep
) -> StreamOut:
    source = _source(body)
    assert source is not None  # StreamCreate.source is required
    view = await streams.create_stream(
        uow,
        health,
        streams.CreateStreamRequest(
            owner_id=owner,
            name=body.name,
            source=source,
            camera_id=body.camera_id,
            watchlist_ids=tuple(body.watchlist_ids),
            sampling=_sampling(body) or streams.SamplingSpec(),
            roi=_roi(body) or (),
            detect_classes=tuple(body.detect_classes),
        ),
    )
    return StreamOut.of(view)


@router.get("/streams")
async def list_streams(owner: OwnerDep, uow: UowDep, health: HealthDep) -> list[StreamOut]:
    return [StreamOut.of(v) for v in await streams.list_streams(uow, health, owner)]


@router.get("/streams/{stream_id}")
async def get_stream(stream_id: str, owner: OwnerDep, uow: UowDep, health: HealthDep) -> StreamOut:
    return StreamOut.of(await streams.get_stream(uow, health, owner, stream_id))


@router.patch("/streams/{stream_id}")
async def update_stream(
    stream_id: str, body: StreamUpdate, owner: OwnerDep, uow: UowDep, health: HealthDep
) -> StreamOut:
    view = await streams.update_stream(
        uow,
        health,
        owner,
        stream_id,
        streams.UpdateStreamRequest(
            name=body.name,
            source=_source(body),
            camera_id=body.camera_id,
            watchlist_ids=None if body.watchlist_ids is None else tuple(body.watchlist_ids),
            sampling=_sampling(body),
            roi=_roi(body),
            detect_classes=(None if body.detect_classes is None else tuple(body.detect_classes)),
            enabled=body.enabled,
        ),
    )
    return StreamOut.of(view)


@router.put("/streams/{stream_id}/state")
async def set_stream_state(
    stream_id: str, body: StreamStateIn, owner: OwnerDep, uow: UowDep, health: HealthDep
) -> StreamOut:
    view = await streams.set_stream_state(uow, health, owner, stream_id, running=body.running)
    return StreamOut.of(view)


@router.delete("/streams/{stream_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stream(stream_id: str, owner: OwnerDep, uow: UowDep) -> None:
    await streams.delete_stream(uow, owner, stream_id)
