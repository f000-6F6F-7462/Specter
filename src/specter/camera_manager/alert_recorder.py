"""Records the alerts that camera processes publish into the database."""

import asyncio
from functools import partial

from specter.entities.alerts import Alert, IdentityMatchAlert, RuleAlert
from specter.entities.geometry import NormalizedBoundingBox
from specter.messaging.message_bus import JobDelivery, JobWorker, MessageBus
from specter.messaging.messages import (
    MatchConfirmedMessage,
    MessageBoundingBox,
    RuleTriggeredMessage,
)
from specter.messaging.streams import MATCH_ALERTS_CONSUMER, RULE_ALERTS_CONSUMER
from specter.storage.alerts import save_alert
from specter.storage.database import DatabaseThread


def build_identity_match_alert(message: MatchConfirmedMessage) -> IdentityMatchAlert:
    """Returns the alert that a match event records, identified by the event's message id."""
    return IdentityMatchAlert(
        id=message.message_id,
        owner_id=message.owner_id,
        camera_id=message.camera_id,
        track_id=message.track_id,
        watchlist_id=message.watchlist_id,
        target_id=message.target_id,
        modality=message.modality,
        similarity_ratio=message.similarity_ratio,
        margin_ratio=message.margin_ratio,
        object_class=message.object_class,
        bounding_box=_build_bounding_box(message.bounding_box),
        frame_captured_at=message.frame_captured_at,
        created_at=message.occurred_at,
        snapshot_path=message.snapshot_path,
    )


def build_rule_alert(message: RuleTriggeredMessage) -> RuleAlert:
    """Returns the alert that a rule event records, identified by the event's message id."""
    return RuleAlert(
        id=message.message_id,
        owner_id=message.owner_id,
        camera_id=message.camera_id,
        track_id=message.track_id,
        rule_id=message.rule_id,
        rule_kind=message.rule_kind,
        zone_id=message.zone_id,
        object_class=message.object_class,
        bounding_box=_build_bounding_box(message.bounding_box),
        dwell_seconds=message.dwell_seconds,
        crossing_direction=message.crossing_direction,
        frame_captured_at=message.frame_captured_at,
        created_at=message.occurred_at,
        snapshot_path=message.snapshot_path,
    )


class AlertRecorder:
    """Saves every published match and rule event as an alert, for the API to list and review.

    Camera processes never write alerts themselves, so the database has this single writer of
    alerts, and events wait in JetStream while it is busy or down. A redelivered event is saved
    once, because the alert takes the event's message id.
    """

    def __init__(self, message_bus: MessageBus, database_thread: DatabaseThread) -> None:
        self._message_bus = message_bus
        self._database_thread = database_thread

    async def run(self, shutdown_requested: asyncio.Event) -> None:
        """Records alerts until shutdown is requested."""
        await asyncio.gather(
            JobWorker(self._message_bus, MATCH_ALERTS_CONSUMER, self._record_match).run(
                shutdown_requested
            ),
            JobWorker(self._message_bus, RULE_ALERTS_CONSUMER, self._record_rule_firing).run(
                shutdown_requested
            ),
        )

    async def _record_match(self, raw_message: bytes, _delivery: JobDelivery) -> None:
        await self._save(
            build_identity_match_alert(MatchConfirmedMessage.model_validate_json(raw_message))
        )

    async def _record_rule_firing(self, raw_message: bytes, _delivery: JobDelivery) -> None:
        await self._save(build_rule_alert(RuleTriggeredMessage.model_validate_json(raw_message)))

    async def _save(self, alert: Alert) -> None:
        await asyncio.get_running_loop().run_in_executor(
            self._database_thread.executor, partial(save_alert, alert)
        )


def _build_bounding_box(bounding_box: MessageBoundingBox) -> NormalizedBoundingBox:
    return NormalizedBoundingBox(
        x=bounding_box.x, y=bounding_box.y, width=bounding_box.width, height=bounding_box.height
    )
