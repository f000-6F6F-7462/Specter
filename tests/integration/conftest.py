"""Integration-suite fixtures: a real Unit of Work over in-memory SQLite."""

from collections.abc import AsyncIterator

import pytest

from specter.application.ports import UnitOfWork, UnitOfWorkFactory
from specter.infrastructure.db import create_all, create_engine, session_factory
from specter.infrastructure.db.uow import SqlAlchemyUnitOfWork


@pytest.fixture
async def uow_factory(tmp_path, monkeypatch) -> AsyncIterator[UnitOfWorkFactory]:
    monkeypatch.chdir(tmp_path)
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    sessions = session_factory(engine)

    def factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    yield factory
    await engine.dispose()
