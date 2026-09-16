"""Alerts of an owner: listing them page by page, reviewing them and reading their snapshots."""

import asyncio
import base64
import binascii
import json
from dataclasses import replace
from datetime import datetime
from functools import partial
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import AwareDatetime, BaseModel

from specter.api.dependencies import ApiServices, ServicesDependency
from specter.api.ownership import get_owner_alert
from specter.api.schemas import RequestModel
from specter.core.errors import InvalidEntityError, NotFoundError
from specter.entities.alerts import (
    Alert,
    AlertReview,
    Disposition,
    IdentityMatchAlert,
    RuleAlert,
)
from specter.entities.rules import CrossingDirection, RuleKind
from specter.entities.targets import EmbeddingModality
from specter.storage.alerts import (
    AlertFilter,
    AlertPosition,
    list_identity_match_alerts,
    list_rule_alerts,
    save_alert_review,
)

router = APIRouter(prefix="/owners/{owner_id}/alerts", tags=["alerts"])

DEFAULT_PAGE_SIZE = 50
MAXIMUM_PAGE_SIZE = 200
SNAPSHOT_MEDIA_TYPE = "image/jpeg"


class BoundingBoxResponse(BaseModel):
    """A bounding box as fractions of the frame's width and height."""

    x: float
    y: float
    width: float
    height: float


class ReviewResponse(BaseModel):
    """How a person has reviewed an alert so far."""

    disposition: Disposition
    is_acknowledged: bool
    note: str | None


class AlertResponse(BaseModel):
    """An alert of either kind; fields of the other kind are null."""

    id: str
    kind: Literal["identity_match", "rule"]
    owner_id: str
    camera_id: str
    track_id: int
    object_class: str
    bounding_box: BoundingBoxResponse
    frame_captured_at: datetime
    created_at: datetime
    has_snapshot: bool
    review: ReviewResponse
    watchlist_id: str | None = None
    target_id: str | None = None
    modality: EmbeddingModality | None = None
    similarity_ratio: float | None = None
    margin_ratio: float | None = None
    rule_id: str | None = None
    rule_kind: RuleKind | None = None
    zone_id: str | None = None
    dwell_seconds: float | None = None
    crossing_direction: CrossingDirection | None = None


class AlertPageResponse(BaseModel):
    """One page of alerts, newest first."""

    alerts: list[AlertResponse]
    # Passed back as ``cursor`` to read the next, older page; None on the last page.
    next_cursor: str | None


class ResolutionBody(RequestModel):
    """A reviewer's verdict on an alert."""

    disposition: Literal[Disposition.TRUE_POSITIVE, Disposition.FALSE_POSITIVE]
    note: str | None = None


@router.get("/identity-matches")
async def list_identity_matches(
    owner_id: str,
    services: ServicesDependency,
    camera_id: Annotated[str | None, Query()] = None,
    disposition: Annotated[Disposition | None, Query()] = None,
    created_since: Annotated[AwareDatetime | None, Query()] = None,
    created_until: Annotated[AwareDatetime | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAXIMUM_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> AlertPageResponse:
    """Lists the owner's identity match alerts, newest first."""
    alert_filter = AlertFilter(
        camera_id=camera_id,
        disposition=disposition,
        created_since=created_since,
        created_until=created_until,
        older_than=decode_cursor(cursor),
    )
    alerts = await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor,
        partial(list_identity_match_alerts, owner_id, alert_filter, limit),
    )
    return build_page(alerts, limit)


@router.get("/rules")
async def list_rule_firings(
    owner_id: str,
    services: ServicesDependency,
    camera_id: Annotated[str | None, Query()] = None,
    disposition: Annotated[Disposition | None, Query()] = None,
    created_since: Annotated[AwareDatetime | None, Query()] = None,
    created_until: Annotated[AwareDatetime | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAXIMUM_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> AlertPageResponse:
    """Lists the owner's rule alerts, newest first."""
    alert_filter = AlertFilter(
        camera_id=camera_id,
        disposition=disposition,
        created_since=created_since,
        created_until=created_until,
        older_than=decode_cursor(cursor),
    )
    alerts = await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor,
        partial(list_rule_alerts, owner_id, alert_filter, limit),
    )
    return build_page(alerts, limit)


@router.get("/{alert_id}")
async def read_alert(owner_id: str, alert_id: str, services: ServicesDependency) -> AlertResponse:
    """Returns one of the owner's alerts."""
    return build_alert_response(await get_owner_alert(services, owner_id, alert_id))


@router.post("/{alert_id}/acknowledge")
async def acknowledge_alert(
    owner_id: str, alert_id: str, services: ServicesDependency
) -> AlertResponse:
    """Marks the alert as seen, without a verdict."""
    alert = await get_owner_alert(services, owner_id, alert_id)
    return await store_review(services, alert, alert.review.acknowledge())


@router.post("/{alert_id}/resolve")
async def resolve_alert(
    owner_id: str, alert_id: str, body: ResolutionBody, services: ServicesDependency
) -> AlertResponse:
    """Records the reviewer's verdict, which also acknowledges the alert."""
    alert = await get_owner_alert(services, owner_id, alert_id)
    return await store_review(services, alert, alert.review.resolve(body.disposition, body.note))


@router.get(
    "/{alert_id}/snapshot",
    response_class=FileResponse,
    responses={200: {"content": {SNAPSHOT_MEDIA_TYPE: {}}}},
)
async def read_snapshot(owner_id: str, alert_id: str, services: ServicesDependency) -> FileResponse:
    """Returns the frame the alert was raised on.

    Raises:
        NotFoundError: The alert has no snapshot, or retention already removed it.
    """
    alert = await get_owner_alert(services, owner_id, alert_id)
    if alert.snapshot_path is None:
        raise NotFoundError(f"alert {alert_id} has no snapshot")
    snapshot_file = await asyncio.to_thread(
        services.evidence_store.resolve_snapshot_file, alert.snapshot_path
    )
    return FileResponse(snapshot_file, media_type=SNAPSHOT_MEDIA_TYPE)


async def store_review(services: ApiServices, alert: Alert, review: AlertReview) -> AlertResponse:
    """Saves the alert's new review and returns the reviewed alert."""
    await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(save_alert_review, alert.id, review)
    )
    return build_alert_response(replace(alert, review=review))


def encode_cursor(alert: Alert) -> str:
    """Returns an opaque cursor that points after the alert."""
    position = json.dumps([alert.created_at.isoformat(), alert.id])
    return base64.urlsafe_b64encode(position.encode()).decode()


def decode_cursor(cursor: str | None) -> AlertPosition | None:
    """Returns the position a cursor points after.

    Raises:
        InvalidEntityError: The cursor was not produced by this API.
    """
    if cursor is None:
        return None
    try:
        created_at_text, alert_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return AlertPosition(created_at=datetime.fromisoformat(created_at_text), alert_id=alert_id)
    except (binascii.Error, ValueError, TypeError) as error:
        raise InvalidEntityError("the cursor is not valid") from error


def build_page(alerts: list[IdentityMatchAlert] | list[RuleAlert], limit: int) -> AlertPageResponse:
    """Returns a page of alerts, with a cursor when the page is full."""
    return AlertPageResponse(
        alerts=[build_alert_response(alert) for alert in alerts],
        next_cursor=encode_cursor(alerts[-1]) if len(alerts) == limit else None,
    )


def build_alert_response(alert: Alert) -> AlertResponse:
    """Returns the alert's response."""
    shared_fields = {
        "id": alert.id,
        "owner_id": alert.owner_id,
        "camera_id": alert.camera_id,
        "track_id": alert.track_id,
        "object_class": alert.object_class,
        "bounding_box": BoundingBoxResponse(
            x=alert.bounding_box.x,
            y=alert.bounding_box.y,
            width=alert.bounding_box.width,
            height=alert.bounding_box.height,
        ),
        "frame_captured_at": alert.frame_captured_at,
        "created_at": alert.created_at,
        "has_snapshot": alert.snapshot_path is not None,
        "review": ReviewResponse(
            disposition=alert.review.disposition,
            is_acknowledged=alert.review.is_acknowledged,
            note=alert.review.note,
        ),
    }
    match alert:
        case IdentityMatchAlert():
            return AlertResponse(
                kind="identity_match",
                watchlist_id=alert.watchlist_id,
                target_id=alert.target_id,
                modality=alert.modality,
                similarity_ratio=alert.similarity_ratio,
                margin_ratio=alert.margin_ratio,
                **shared_fields,
            )
        case RuleAlert():
            return AlertResponse(
                kind="rule",
                rule_id=alert.rule_id,
                rule_kind=alert.rule_kind,
                zone_id=alert.zone_id,
                dwell_seconds=alert.dwell_seconds,
                crossing_direction=alert.crossing_direction,
                **shared_fields,
            )
