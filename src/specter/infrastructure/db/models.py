"""ORM row models — persistence-shaped, translated to/from domain objects by mappers."""

# pylint: disable=unsubscriptable-object  # pylint mis-parses SQLAlchemy's Mapped[...] generic

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from specter.infrastructure.db.base import Base, utcnow

type Json = dict[str, Any]
type JsonList = list[Any]

_TS = DateTime(timezone=True)


class WatchlistRow(Base):
    __tablename__ = "watchlists"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32))
    match_threshold: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(_TS, default=None)


class TargetRow(Base):
    __tablename__ = "targets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    watchlist_id: Mapped[str] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    meta: Mapped[Json] = mapped_column("metadata", JSON, default=dict)
    batch_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(_TS, default=None)

    images: Mapped[list["ReferenceImageRow"]] = relationship(
        back_populates="target",
        cascade="all, delete-orphan",
        order_by="ReferenceImageRow.created_at",
        lazy="selectin",
    )


class ReferenceImageRow(Base):
    __tablename__ = "reference_images"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("targets.id", ondelete="CASCADE"), index=True)
    blob_key: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    quality: Mapped[Json | None] = mapped_column(JSON, default=None)
    rejection_reason: Mapped[str | None] = mapped_column(String(64), default=None)
    model_version: Mapped[str | None] = mapped_column(String(128), default=None)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)

    target: Mapped[TargetRow] = relationship(back_populates="images")


class StreamRow(Base):
    __tablename__ = "streams"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(255))
    camera_id: Mapped[str | None] = mapped_column(String(128), default=None)
    protocol: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(String(1024))
    transport: Mapped[str] = mapped_column(String(8), default="tcp")
    # Phase 6 encrypts this at rest (Fernet); stored as JSON {username, password} for now.
    credentials: Mapped[Json | None] = mapped_column(JSON, default=None)
    sampling: Mapped[Json] = mapped_column(JSON, default=dict)
    roi: Mapped[JsonList] = mapped_column(JSON, default=list)
    watchlist_ids: Mapped[JsonList] = mapped_column(JSON, default=list)
    detect_classes: Mapped[JsonList] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    desired_state: Mapped[str] = mapped_column(String(16), default="stopped")
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


class AlertRow(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128))
    stream_id: Mapped[str] = mapped_column(String(64))
    watchlist_id: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(64))
    similarity: Mapped[float] = mapped_column(Float)
    calibrated_confidence: Mapped[float | None] = mapped_column(Float, default=None)
    bbox: Mapped[Json] = mapped_column(JSON)
    track_id: Mapped[int] = mapped_column(Integer)
    frame_ts: Mapped[datetime] = mapped_column(_TS)
    snapshot_key: Mapped[str | None] = mapped_column(String(512), default=None)
    crop_key: Mapped[str | None] = mapped_column(String(512), default=None)
    clip_key: Mapped[str | None] = mapped_column(String(512), default=None)
    disposition: Mapped[str] = mapped_column(String(32), default="unreviewed")
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)

    __table_args__ = (
        Index("ix_alerts_owner_id_created_at", "owner_id", "created_at"),
        Index("ix_alerts_stream_id_created_at", "stream_id", "created_at"),
    )
