"""add watchlists.metadata

Revision ID: 0002_watchlist_metadata
Revises: 0001_initial
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_watchlist_metadata"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "watchlists",
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    with op.batch_alter_table("watchlists") as batch_op:
        batch_op.alter_column("metadata", server_default=None)


def downgrade() -> None:
    op.drop_column("watchlists", "metadata")
