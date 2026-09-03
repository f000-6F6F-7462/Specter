from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from specter.application import alerts
from specter.domain.alerts import Disposition
from specter.entrypoints.http.deps import OwnerDep, UowDep
from specter.entrypoints.http.schemas import AlertOut, AlertPageOut, ResolveIn

router = APIRouter(tags=["alerts"])


@router.get("/alerts")
async def list_alerts(
    owner: OwnerDep,
    uow: UowDep,
    stream_id: str | None = None,
    watchlist_id: str | None = None,
    disposition: Disposition | None = None,
    min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> AlertPageOut:
    page = await alerts.list_alerts(
        uow,
        owner,
        alerts.AlertFilter(
            stream_id=stream_id,
            watchlist_id=watchlist_id,
            disposition=disposition,
            min_confidence=min_confidence,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        ),
    )
    return AlertPageOut.of(page)


@router.get("/alerts/{alert_id}")
async def get_alert(alert_id: str, owner: OwnerDep, uow: UowDep) -> AlertOut:
    return AlertOut.of(await alerts.get_alert(uow, owner, alert_id))


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: str, owner: OwnerDep, uow: UowDep) -> AlertOut:
    return AlertOut.of(await alerts.ack_alert(uow, owner, alert_id))


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: str, body: ResolveIn, owner: OwnerDep, uow: UowDep) -> AlertOut:
    view = await alerts.resolve_alert(uow, owner, alert_id, body.disposition, body.note)
    return AlertOut.of(view)
