"""SQLAlchemy persistence: engine, models, mappers, repositories, unit of work.

Importing this package imports ``models`` as a side effect, registering every table
on ``Base.metadata`` (needed by ``create_all`` and Alembic autogenerate).
"""

from specter.infrastructure.db import models
from specter.infrastructure.db.base import Base
from specter.infrastructure.db.engine import (
    SessionFactory,
    create_all,
    create_engine,
    drop_all,
    session_factory,
)
from specter.infrastructure.db.uow import SqlAlchemyUnitOfWork

__all__ = [
    "Base",
    "SessionFactory",
    "SqlAlchemyUnitOfWork",
    "create_all",
    "create_engine",
    "drop_all",
    "models",
    "session_factory",
]
