"""Catalog use cases.

Each is a plain async function: dependencies first (``uow_factory``, then ``blob``/
``bus``/``health`` when needed), then the request. One call == one Unit of Work == one
transaction. Cross-owner access is denied by comparing ``owner_id`` on every read.

Mutations that change what a running stream matches against (watchlist threshold/
rename, delete, target enable/disable/rename, delete) bump that watchlist's version in
``HealthStore`` — see ``application.pipeline.runner`` for the reader side.
"""

from datetime import UTC, datetime

from specter.application.catalog.dto import (
    AddImagesRequest,
    CreateWatchlistRequest,
    EnrolledTargetView,
    EnrollmentBatchView,
    EnrollTargetsRequest,
    EnrollTargetsResult,
    TargetSpec,
    TargetView,
    UpdateTargetRequest,
    UpdateWatchlistRequest,
    UploadedImage,
    WatchlistView,
)
from specter.application.ports import (
    BlobStore,
    EventBus,
    HealthStore,
    UnitOfWork,
    UnitOfWorkFactory,
)
from specter.contracts import EnrollJobMessage
from specter.contracts.streams import JOBS_ENROLL
from specter.core.errors import NotFoundError, RuleViolation
from specter.core.ids import new_id
from specter.domain.catalog import (
    EnrollmentStatus,
    ImageStatus,
    ReferenceImage,
    Target,
    TargetType,
    Watchlist,
)

_MODALITY = {TargetType.PERSON: "face"}


def _blob_key(owner_id: str, image_id: str) -> str:
    now = datetime.now(UTC)
    return f"blobs/{owner_id}/reference/{now:%Y/%m}/{image_id}.jpg"


def _modality(target_type: TargetType) -> str:
    try:
        return _MODALITY[target_type]
    except KeyError:
        raise RuleViolation(
            f"enrollment for {target_type.value!r} targets is not supported yet"
        ) from None


def _worst_status(states: list[EnrollmentStatus]) -> EnrollmentStatus:
    for state in (EnrollmentStatus.FAILED, EnrollmentStatus.QUEUED, EnrollmentStatus.PARTIAL):
        if state in states:
            return state
    return EnrollmentStatus.READY


#  watchlists


async def create_watchlist(
    uow_factory: UnitOfWorkFactory, req: CreateWatchlistRequest
) -> WatchlistView:
    watchlist = Watchlist(
        id=new_id("wl"),
        owner_id=req.owner_id,
        name=req.name,
        type=req.type,
        kind=req.kind,
        match_threshold=req.match_threshold,
        metadata=dict(req.metadata),
    )
    async with uow_factory() as uow:
        await uow.watchlists.add(watchlist)
    return WatchlistView.of(watchlist, target_count=0)


async def list_watchlists(uow_factory: UnitOfWorkFactory, owner_id: str) -> list[WatchlistView]:
    async with uow_factory() as uow:
        watchlists = await uow.watchlists.list_for_owner(owner_id)
        return [
            WatchlistView.of(wl, target_count=await uow.targets.count_for_watchlist(wl.id))
            for wl in watchlists
        ]


async def get_watchlist(
    uow_factory: UnitOfWorkFactory, owner_id: str, watchlist_id: str
) -> WatchlistView:
    async with uow_factory() as uow:
        watchlist = await _load_watchlist(uow, owner_id, watchlist_id)
        count = await uow.targets.count_for_watchlist(watchlist_id)
    return WatchlistView.of(watchlist, target_count=count)


async def update_watchlist(
    uow_factory: UnitOfWorkFactory,
    health: HealthStore,
    owner_id: str,
    watchlist_id: str,
    req: UpdateWatchlistRequest,
) -> WatchlistView:
    async with uow_factory() as uow:
        watchlist = await _load_watchlist(uow, owner_id, watchlist_id)
        if req.name is not None:
            watchlist.rename(req.name)
        if req.match_threshold is not None:
            watchlist.set_threshold(req.match_threshold)
        if req.metadata is not None:
            watchlist.metadata = dict(req.metadata)
        await uow.watchlists.update(watchlist)
        count = await uow.targets.count_for_watchlist(watchlist_id)
    # A running stream polls this to notice the edit without waiting out
    # directory_refresh_s — see StreamDirectory / run_stream.
    await health.bump_watchlist_version(watchlist_id)
    return WatchlistView.of(watchlist, target_count=count)


async def delete_watchlist(
    uow_factory: UnitOfWorkFactory, health: HealthStore, owner_id: str, watchlist_id: str
) -> None:
    async with uow_factory() as uow:
        await _load_watchlist(uow, owner_id, watchlist_id)
        await uow.watchlists.soft_delete(watchlist_id)
    await health.bump_watchlist_version(watchlist_id)


#  targets


async def enroll_targets(
    uow_factory: UnitOfWorkFactory,
    blob: BlobStore,
    bus: EventBus,
    req: EnrollTargetsRequest,
) -> EnrollTargetsResult:
    if not req.targets:
        raise RuleViolation("no targets in enrollment request")
    files_by_name = {image.name: image for image in req.images}
    batch_id = new_id("bat")

    async with uow_factory() as uow:
        watchlist = await _load_watchlist(uow, req.owner_id, req.watchlist_id)
        targets = [
            await _enroll_one(uow, blob, watchlist, spec, files_by_name, batch_id)
            for spec in req.targets
        ]

    # After commit: dispatch one job per image so the enroll worker can embed it.
    for target, spec in zip(targets, req.targets, strict=True):
        modality = _modality(spec.type)
        for image in target.images:
            await bus.publish(
                JOBS_ENROLL,
                target.id,
                EnrollJobMessage(
                    event_id=new_id("evt"),
                    occurred_at=datetime.now(UTC),
                    owner_id=req.owner_id,
                    batch_id=batch_id,
                    target_id=target.id,
                    image_id=image.id,
                    blob_key=image.blob_key,
                    modality=modality,
                ),
            )

    items = tuple(
        EnrolledTargetView(
            ref=spec.ref,
            target_id=target.id,
            status=target.status,
            image_ids=tuple(image.id for image in target.images),
        )
        for target, spec in zip(targets, req.targets, strict=True)
    )
    return EnrollTargetsResult(batch_id=batch_id, items=items)


async def _enroll_one(
    uow: UnitOfWork,
    blob: BlobStore,
    watchlist: Watchlist,
    spec: TargetSpec,
    files_by_name: dict[str, UploadedImage],
    batch_id: str,
) -> Target:
    if not spec.image_names:
        raise RuleViolation(f"target {spec.ref!r} has no images")
    _modality(spec.type)  # reject unsupported target types before any writes
    target = Target(
        id=new_id("tgt"),
        watchlist_id=watchlist.id,
        label=spec.label,
        type=spec.type,
        metadata=dict(spec.metadata),
        batch_id=batch_id,
    )
    for name in spec.image_names:
        uploaded = files_by_name.get(name)
        if uploaded is None:
            raise RuleViolation(f"target {spec.ref!r} references unknown file {name!r}")
        image_id = new_id("img")
        key = _blob_key(watchlist.owner_id, image_id)
        await blob.put(key, uploaded.data, uploaded.content_type)
        target.images.append(ReferenceImage(id=image_id, blob_key=key, status=ImageStatus.PENDING))
    await uow.targets.add(target)
    return target


async def list_targets(
    uow_factory: UnitOfWorkFactory,
    owner_id: str,
    watchlist_id: str,
    *,
    status: EnrollmentStatus | None = None,
) -> list[TargetView]:
    # Not paginated: a watchlist's target set is bounded by what the owner manages.
    async with uow_factory() as uow:
        await _load_watchlist(uow, owner_id, watchlist_id)
        targets = await uow.targets.list_for_watchlist(watchlist_id, status=status)
    return [TargetView.of(t) for t in targets]


async def get_target(uow_factory: UnitOfWorkFactory, owner_id: str, target_id: str) -> TargetView:
    async with uow_factory() as uow:
        target = await _load_target(uow, owner_id, target_id)
    return TargetView.of(target)


async def update_target(
    uow_factory: UnitOfWorkFactory,
    health: HealthStore,
    owner_id: str,
    target_id: str,
    req: UpdateTargetRequest,
) -> TargetView:
    async with uow_factory() as uow:
        target = await _load_target(uow, owner_id, target_id)
        if req.label is not None:
            target.rename(req.label)
        if req.enabled is not None:
            target.enabled = req.enabled
        if req.metadata is not None:
            target.metadata = dict(req.metadata)
        await uow.targets.update(target)
    await health.bump_watchlist_version(target.watchlist_id)
    return TargetView.of(target)


async def delete_target(
    uow_factory: UnitOfWorkFactory, health: HealthStore, owner_id: str, target_id: str
) -> None:
    async with uow_factory() as uow:
        target = await _load_target(uow, owner_id, target_id)
        await uow.targets.soft_delete(target_id)
    await health.bump_watchlist_version(target.watchlist_id)


async def batch_delete_targets(
    uow_factory: UnitOfWorkFactory,
    health: HealthStore,
    owner_id: str,
    watchlist_id: str,
    target_ids: list[str],
) -> int:
    deleted = 0
    async with uow_factory() as uow:
        await _load_watchlist(uow, owner_id, watchlist_id)
        for target_id in target_ids:
            target = await uow.targets.get(target_id)
            if target is None or target.watchlist_id != watchlist_id:
                continue
            await uow.targets.soft_delete(target_id)
            deleted += 1
    if deleted:
        await health.bump_watchlist_version(watchlist_id)
    return deleted


async def add_target_images(
    uow_factory: UnitOfWorkFactory, blob: BlobStore, req: AddImagesRequest
) -> TargetView:
    if not req.images:
        raise RuleViolation("no images supplied")
    async with uow_factory() as uow:
        target = await _load_target(uow, req.owner_id, req.target_id)
        for uploaded in req.images:
            image_id = new_id("img")
            key = _blob_key(req.owner_id, image_id)
            await blob.put(key, uploaded.data, uploaded.content_type)
            target.images.append(
                ReferenceImage(id=image_id, blob_key=key, status=ImageStatus.PENDING)
            )
        await uow.targets.update(target)
    return TargetView.of(target)


async def delete_target_image(
    uow_factory: UnitOfWorkFactory, owner_id: str, target_id: str, image_id: str
) -> TargetView:
    async with uow_factory() as uow:
        target = await _load_target(uow, owner_id, target_id)
        if not target.remove_image(image_id):
            raise NotFoundError(f"image {image_id} on target {target_id}")
        await uow.targets.update(target)
    return TargetView.of(target)


async def get_enrollment_batch(
    uow_factory: UnitOfWorkFactory, owner_id: str, batch_id: str
) -> EnrollmentBatchView:
    async with uow_factory() as uow:
        targets = await uow.targets.list_by_batch(batch_id)
        for watchlist_id in {t.watchlist_id for t in targets}:
            await _load_watchlist(uow, owner_id, watchlist_id)  # 404 unless owner's
    if not targets:
        raise NotFoundError(f"enrollment batch {batch_id}")
    views = tuple(TargetView.of(t) for t in targets)
    state = _worst_status([v.status for v in views])
    return EnrollmentBatchView(batch_id=batch_id, state=state, targets=views)


#  helpers


async def _load_watchlist(uow: UnitOfWork, owner_id: str, watchlist_id: str) -> Watchlist:
    watchlist = await uow.watchlists.get(watchlist_id)
    if watchlist is None or watchlist.owner_id != owner_id:
        raise NotFoundError(f"watchlist {watchlist_id}")
    return watchlist


async def _load_target(uow: UnitOfWork, owner_id: str, target_id: str) -> Target:
    target = await uow.targets.get(target_id)
    if target is None:
        raise NotFoundError(f"target {target_id}")
    await _load_watchlist(uow, owner_id, target.watchlist_id)  # 404 unless owner's
    return target
