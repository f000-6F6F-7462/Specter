"""The SQLAlchemy Unit of Work.

One ``async with uow:`` == one session == one transaction. Clean exit commits, an
exception rolls back.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from specter.application.ports import AlertRepo, StreamRepo, TargetRepo, WatchlistRepo
from specter.core.errors import SpecterError
from specter.infrastructure.db.engine import SessionFactory
from specter.infrastructure.db.repositories import (
    SqlAlchemyAlertRepo,
    SqlAlchemyStreamRepo,
    SqlAlchemyTargetRepo,
    SqlAlchemyWatchlistRepo,
)


class SqlAlchemyUnitOfWork:
    watchlists: WatchlistRepo
    targets: TargetRepo
    streams: StreamRepo
    alerts: AlertRepo

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        self._session = self._session_factory()
        self.watchlists = SqlAlchemyWatchlistRepo(self._session)
        self.targets = SqlAlchemyTargetRepo(self._session)
        self.streams = SqlAlchemyStreamRepo(self._session)
        self.alerts = SqlAlchemyAlertRepo(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        session = self._require_session()
        try:
            if exc_type is None:
                await session.commit()
            else:
                await session.rollback()
        finally:
            await session.close()
            self._session = None

    async def commit(self) -> None:
        await self._require_session().commit()

    async def rollback(self) -> None:
        await self._require_session().rollback()

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            raise SpecterError("unit of work used outside 'async with'")
        return self._session
