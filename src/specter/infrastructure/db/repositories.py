"""SQLAlchemy implementations of the repository ports.

Every method returns domain objects, never Rows. Queries exclude soft-deleted rows.
The computed ``Target.status`` filter is applied in Python because it is derived, not
stored.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from specter.core.errors import NotFoundError
from specter.domain.alerts import Alert, Disposition
from specter.domain.catalog import EnrollmentStatus, Target, Watchlist
from specter.domain.streams import StreamConfig
from specter.infrastructure.db import mappers
from specter.infrastructure.db.base import utcnow
from specter.infrastructure.db.models import (
    AlertRow,
    StreamRow,
    TargetRow,
    WatchlistRow,
)


class SqlAlchemyWatchlistRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, watchlist_id: str) -> Watchlist | None:
        row = await self._session.get(WatchlistRow, watchlist_id)
        if row is None or row.deleted_at is not None:
            return None
        return mappers.watchlist_to_domain(row)

    async def list_for_owner(self, owner_id: str) -> list[Watchlist]:
        stmt = (
            select(WatchlistRow)
            .where(WatchlistRow.owner_id == owner_id, WatchlistRow.deleted_at.is_(None))
            .order_by(WatchlistRow.created_at)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [mappers.watchlist_to_domain(r) for r in rows]

    async def add(self, watchlist: Watchlist) -> None:
        self._session.add(mappers.watchlist_to_row(watchlist))

    async def update(self, watchlist: Watchlist) -> None:
        row = await self._session.get(WatchlistRow, watchlist.id)
        if row is None or row.deleted_at is not None:
            raise NotFoundError(f"watchlist {watchlist.id}")
        mappers.apply_watchlist(row, watchlist)

    async def soft_delete(self, watchlist_id: str) -> None:
        row = await self._session.get(WatchlistRow, watchlist_id)
        if row is None or row.deleted_at is not None:
            raise NotFoundError(f"watchlist {watchlist_id}")
        row.deleted_at = utcnow()


class SqlAlchemyTargetRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, target_id: str) -> Target | None:
        row = await self._s.get(TargetRow, target_id)
        if row is None or row.deleted_at is not None:
            return None
        return mappers.target_to_domain(row)

    async def list_for_watchlist(
        self, watchlist_id: str, *, status: EnrollmentStatus | None = None
    ) -> list[Target]:
        stmt = (
            select(TargetRow)
            .where(
                TargetRow.watchlist_id == watchlist_id,
                TargetRow.deleted_at.is_(None),
            )
            .order_by(TargetRow.created_at)
        )
        targets = [mappers.target_to_domain(r) for r in (await self._s.scalars(stmt)).all()]
        if status is not None:
            targets = [t for t in targets if t.status is status]
        return targets

    async def list_by_batch(self, batch_id: str) -> list[Target]:
        stmt = (
            select(TargetRow)
            .where(TargetRow.batch_id == batch_id, TargetRow.deleted_at.is_(None))
            .order_by(TargetRow.created_at)
        )
        return [mappers.target_to_domain(r) for r in (await self._s.scalars(stmt)).all()]

    async def count_for_watchlist(self, watchlist_id: str) -> int:
        stmt = (
            select(func.count())  # pylint: disable=not-callable
            .select_from(TargetRow)
            .where(TargetRow.watchlist_id == watchlist_id, TargetRow.deleted_at.is_(None))
        )
        return int((await self._s.scalar(stmt)) or 0)

    async def add(self, target: Target) -> None:
        self._s.add(mappers.target_to_row(target))

    async def update(self, target: Target) -> None:
        row = await self._s.get(TargetRow, target.id)
        if row is None or row.deleted_at is not None:
            raise NotFoundError(f"target {target.id}")
        mappers.sync_target(row, target)

    async def soft_delete(self, target_id: str) -> None:
        row = await self._s.get(TargetRow, target_id)
        if row is None or row.deleted_at is not None:
            raise NotFoundError(f"target {target_id}")
        row.deleted_at = utcnow()


class SqlAlchemyStreamRepo:
    def __init__(
        self, session: AsyncSession, secret_key: str = "dev-only-fernet-key-change-me"
    ) -> None:
        self._s = session
        self._secret_key = secret_key

    async def get(self, stream_id: str) -> StreamConfig | None:
        row = await self._s.get(StreamRow, stream_id)
        return mappers.stream_to_domain(row, self._secret_key) if row is not None else None

    async def list_for_owner(self, owner_id: str) -> list[StreamConfig]:
        stmt = (
            select(StreamRow).where(StreamRow.owner_id == owner_id).order_by(StreamRow.created_at)
        )
        return [
            mappers.stream_to_domain(r, self._secret_key)
            for r in (await self._s.scalars(stmt)).all()
        ]

    async def list_enabled(self) -> list[StreamConfig]:
        stmt = select(StreamRow).where(StreamRow.enabled.is_(True))
        return [
            mappers.stream_to_domain(r, self._secret_key)
            for r in (await self._s.scalars(stmt)).all()
        ]

    async def add(self, stream: StreamConfig) -> None:
        self._s.add(mappers.stream_to_row(stream, self._secret_key))

    async def update(self, stream: StreamConfig) -> None:
        row = await self._s.get(StreamRow, stream.id)
        if row is None:
            raise NotFoundError(f"stream {stream.id}")
        mappers.apply_stream(row, stream, self._secret_key)

    async def delete(self, stream_id: str) -> None:
        row = await self._s.get(StreamRow, stream_id)
        if row is None:
            raise NotFoundError(f"stream {stream_id}")
        await self._s.delete(row)


class SqlAlchemyAlertRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, alert_id: str) -> Alert | None:
        row = await self._s.get(AlertRow, alert_id)
        return mappers.alert_to_domain(row) if row is not None else None

    async def add(self, alert: Alert) -> None:
        self._s.add(mappers.alert_to_row(alert))

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        stream_id: str | None = None,
        watchlist_id: str | None = None,
        disposition: Disposition | None = None,
        min_confidence: float | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> list[Alert]:
        stmt = select(AlertRow).where(AlertRow.owner_id == owner_id)
        if stream_id is not None:
            stmt = stmt.where(AlertRow.stream_id == stream_id)
        if watchlist_id is not None:
            stmt = stmt.where(AlertRow.watchlist_id == watchlist_id)
        if disposition is not None:
            stmt = stmt.where(AlertRow.disposition == disposition.value)
        if min_confidence is not None:
            stmt = stmt.where(AlertRow.similarity >= min_confidence)
        if since is not None:
            stmt = stmt.where(AlertRow.frame_ts >= since)
        if until is not None:
            stmt = stmt.where(AlertRow.frame_ts <= until)
        if cursor is not None:
            stmt = stmt.where(AlertRow.id < cursor)
        stmt = stmt.order_by(AlertRow.created_at.desc(), AlertRow.id.desc()).limit(limit)
        return [mappers.alert_to_domain(r) for r in (await self._s.scalars(stmt)).all()]

    async def update(self, alert: Alert) -> None:
        row = await self._s.get(AlertRow, alert.id)
        if row is None:
            raise NotFoundError(f"alert {alert.id}")
        mappers.apply_alert(row, alert)
