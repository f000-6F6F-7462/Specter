"""Async engine and session factory construction."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from specter.infrastructure.db.base import Base

SessionFactory = async_sessionmaker[AsyncSession]


def create_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    if url.startswith("sqlite"):
        # A single shared connection so an in-memory database survives across sessions.
        return create_async_engine(
            url,
            echo=echo,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    return create_async_engine(url, echo=echo, pool_pre_ping=True)


def session_factory(engine: AsyncEngine) -> SessionFactory:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def create_all(engine: AsyncEngine) -> None:
    """Create every table. For tests and local bootstrap; production uses Alembic."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
