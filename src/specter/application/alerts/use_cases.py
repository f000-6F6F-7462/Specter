"""Alerts use cases — query, acknowledge, resolve.

Plain async functions: ``uow_factory`` first, then the arguments. Alerts are *written*
by the pipeline; these only read and update them. One call == one Unit of Work;
cross-owner access is denied on every read.
"""

from specter.application.alerts.dto import AlertFilter, AlertPage, AlertView
from specter.application.ports import UnitOfWork, UnitOfWorkFactory
from specter.core.errors import NotFoundError
from specter.domain.alerts import Alert, Disposition

_MAX_LIMIT = 200


async def list_alerts(uow_factory: UnitOfWorkFactory, owner_id: str, flt: AlertFilter) -> AlertPage:
    limit = max(1, min(flt.limit, _MAX_LIMIT))
    async with uow_factory() as uow:
        alerts = await uow.alerts.list_for_owner(
            owner_id,
            stream_id=flt.stream_id,
            watchlist_id=flt.watchlist_id,
            disposition=flt.disposition,
            min_confidence=flt.min_confidence,
            since=flt.since,
            until=flt.until,
            limit=limit,
            cursor=flt.cursor,
        )
    items = tuple(AlertView.of(a) for a in alerts)
    next_cursor = items[-1].id if len(items) == limit else None
    return AlertPage(items=items, next_cursor=next_cursor)


async def get_alert(uow_factory: UnitOfWorkFactory, owner_id: str, alert_id: str) -> AlertView:
    async with uow_factory() as uow:
        alert = await _load(uow, owner_id, alert_id)
    return AlertView.of(alert)


async def ack_alert(uow_factory: UnitOfWorkFactory, owner_id: str, alert_id: str) -> AlertView:
    async with uow_factory() as uow:
        alert = await _load(uow, owner_id, alert_id)
        alert.acknowledge()
        await uow.alerts.update(alert)
    return AlertView.of(alert)


async def resolve_alert(
    uow_factory: UnitOfWorkFactory,
    owner_id: str,
    alert_id: str,
    disposition: Disposition,
    note: str | None,
) -> AlertView:
    async with uow_factory() as uow:
        alert = await _load(uow, owner_id, alert_id)
        alert.resolve(disposition, note)
        await uow.alerts.update(alert)
    return AlertView.of(alert)


async def _load(uow: UnitOfWork, owner_id: str, alert_id: str) -> Alert:
    alert = await uow.alerts.get(alert_id)
    if alert is None or alert.owner_id != owner_id:
        raise NotFoundError(f"alert {alert_id}")
    return alert
