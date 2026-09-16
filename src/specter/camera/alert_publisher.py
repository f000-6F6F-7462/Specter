"""Turns a camera's confirmed matches and fired rules into snapshots and published events."""

import asyncio
import logging
from datetime import UTC, datetime

import av
from nats.errors import Error as NatsError

from specter.camera.person_identifier import ConfirmedMatch
from specter.entities.cameras import Camera
from specter.entities.geometry import NormalizedBoundingBox
from specter.messaging.client import MessageBus
from specter.messaging.messages import (
    MatchConfirmedMessage,
    MessageBoundingBox,
    RuleTriggeredMessage,
    SpecterMessage,
    new_message_id,
)
from specter.storage.evidence import EvidenceStore
from specter.vision.detections import Track
from specter.vision.frames import Frame, FrameImage
from specter.vision.rules import RuleFiring

logger = logging.getLogger(__name__)

JPEG_CODEC_NAME = "mjpeg"
JPEG_PIXEL_FORMAT = "yuvj420p"
DECODED_PIXEL_FORMAT = "bgr24"
# A fixed quantizer of 4 keeps faces sharp at about a third of the size of a near-lossless JPEG.
JPEG_QUANTIZER = "4"


def encode_jpeg(image: FrameImage) -> bytes:
    """Encodes a BGR image as a JPEG with FFmpeg, which the camera process already carries."""
    codec_context = av.Codec(JPEG_CODEC_NAME, "w").create("video")
    codec_context.width = image.shape[1]
    codec_context.height = image.shape[0]
    codec_context.pix_fmt = JPEG_PIXEL_FORMAT
    codec_context.options = {"qmin": JPEG_QUANTIZER, "qmax": JPEG_QUANTIZER}
    video_frame = av.VideoFrame.from_ndarray(image, format=DECODED_PIXEL_FORMAT).reformat(
        format=JPEG_PIXEL_FORMAT
    )
    packets = [*codec_context.encode(video_frame), *codec_context.encode(None)]
    return b"".join(bytes(packet) for packet in packets)


class AlertPublisher:
    """Saves the frame of each alert as evidence and publishes the alert's event.

    The event's message id doubles as the alert id, so the recorder that stores alerts from events
    stores each one once. An alert whose snapshot cannot be written is still published without it.
    """

    def __init__(
        self, camera: Camera, message_bus: MessageBus, evidence_store: EvidenceStore
    ) -> None:
        self._camera = camera
        self._message_bus = message_bus
        self._evidence_store = evidence_store

    async def publish_match(self, frame: Frame, match: ConfirmedMatch) -> None:
        """Publishes a confirmed match with its snapshot."""
        decision = match.decision
        if decision.target_id is None or decision.watchlist_id is None:
            return
        message_id = new_message_id()
        message = MatchConfirmedMessage(
            message_id=message_id,
            occurred_at=datetime.now(UTC),
            owner_id=self._camera.owner_id,
            camera_id=self._camera.id,
            track_id=match.track.track_id,
            watchlist_id=decision.watchlist_id,
            target_id=decision.target_id,
            modality=match.modality,
            # Cosine similarity can fall below zero, which the contract reports as no similarity.
            similarity_ratio=min(max(decision.similarity_ratio, 0.0), 1.0),
            margin_ratio=min(max(decision.margin_ratio, 0.0), 1.0),
            threshold_ratio=match.threshold_ratio,
            object_class=match.track.detection.object_class,
            bounding_box=self._build_bounding_box(frame, match.track),
            first_seen_at=match.track.first_seen_at,
            frame_captured_at=frame.captured_at,
            snapshot_path=await self._save_snapshot(frame, message_id),
        )
        await self._publish(message)

    async def publish_rule_firing(self, frame: Frame, firing: RuleFiring) -> None:
        """Publishes a fired rule with its snapshot."""
        message_id = new_message_id()
        message = RuleTriggeredMessage(
            message_id=message_id,
            occurred_at=datetime.now(UTC),
            owner_id=self._camera.owner_id,
            camera_id=self._camera.id,
            rule_id=firing.rule.id,
            rule_kind=firing.rule.kind,
            zone_id=firing.zone_id,
            track_id=firing.track.track_id,
            object_class=firing.track.detection.object_class,
            bounding_box=self._build_bounding_box(frame, firing.track),
            dwell_seconds=firing.dwell_seconds,
            crossing_direction=firing.crossing_direction,
            frame_captured_at=frame.captured_at,
            snapshot_path=await self._save_snapshot(frame, message_id),
        )
        await self._publish(message)

    async def _save_snapshot(self, frame: Frame, alert_id: str) -> str | None:
        snapshot_path = self._evidence_store.build_snapshot_path(
            self._camera.owner_id, alert_id, frame.captured_at
        )
        try:
            jpeg_bytes = await asyncio.to_thread(encode_jpeg, frame.image)
            await asyncio.to_thread(self._evidence_store.write_snapshot, snapshot_path, jpeg_bytes)
        except (av.FFmpegError, OSError):
            logger.exception("cannot save an alert snapshot", extra={"camera_id": self._camera.id})
            return None
        return snapshot_path

    async def _publish(self, message: SpecterMessage) -> None:
        try:
            await self._message_bus.publish(message)
        except NatsError:
            logger.exception(
                "cannot publish an alert",
                extra={"camera_id": self._camera.id, "message_id": message.message_id},
            )

    @staticmethod
    def _build_bounding_box(frame: Frame, track: Track) -> MessageBoundingBox:
        normalized_box = NormalizedBoundingBox.from_pixels(
            track.detection.bounding_box, frame.width_pixels, frame.height_pixels
        )
        return MessageBoundingBox(
            x=normalized_box.x,
            y=normalized_box.y,
            width=normalized_box.width,
            height=normalized_box.height,
        )
