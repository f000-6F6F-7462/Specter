"""initial schema: watchlists, targets, reference_images, streams, alerts

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "watchlists",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("match_threshold", sa.Float(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("deleted_at", _TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_watchlists"),
    )
    op.create_index("ix_watchlists_owner_id", "watchlists", ["owner_id"])

    op.create_table(
        "targets",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("watchlist_id", sa.String(64), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("batch_id", sa.String(64), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("deleted_at", _TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_targets"),
        sa.ForeignKeyConstraint(
            ["watchlist_id"],
            ["watchlists.id"],
            name="fk_targets_watchlist_id_watchlists",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_targets_watchlist_id", "targets", ["watchlist_id"])
    op.create_index("ix_targets_batch_id", "targets", ["batch_id"])

    op.create_table(
        "reference_images",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("blob_key", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("quality", sa.JSON(), nullable=True),
        sa.Column("rejection_reason", sa.String(64), nullable=True),
        sa.Column("model_version", sa.String(128), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_reference_images"),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["targets.id"],
            name="fk_reference_images_target_id_targets",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_reference_images_target_id", "reference_images", ["target_id"])

    op.create_table(
        "streams",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("camera_id", sa.String(128), nullable=True),
        sa.Column("protocol", sa.String(16), nullable=False),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("transport", sa.String(8), nullable=False),
        sa.Column("credentials", sa.JSON(), nullable=True),
        sa.Column("sampling", sa.JSON(), nullable=False),
        sa.Column("roi", sa.JSON(), nullable=False),
        sa.Column("watchlist_ids", sa.JSON(), nullable=False),
        sa.Column("detect_classes", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("desired_state", sa.String(16), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_streams"),
    )
    op.create_index("ix_streams_owner_id", "streams", ["owner_id"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("stream_id", sa.String(64), nullable=False),
        sa.Column("watchlist_id", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=False),
        sa.Column("calibrated_confidence", sa.Float(), nullable=True),
        sa.Column("bbox", sa.JSON(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("frame_ts", _TS, nullable=False),
        sa.Column("snapshot_key", sa.String(512), nullable=True),
        sa.Column("crop_key", sa.String(512), nullable=True),
        sa.Column("clip_key", sa.String(512), nullable=True),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("acknowledged", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_alerts"),
    )
    op.create_index("ix_alerts_owner_id_created_at", "alerts", ["owner_id", "created_at"])
    op.create_index("ix_alerts_stream_id_created_at", "alerts", ["stream_id", "created_at"])


def downgrade() -> None:
    op.drop_table("alerts")
    op.drop_table("streams")
    op.drop_table("reference_images")
    op.drop_table("targets")
    op.drop_table("watchlists")
