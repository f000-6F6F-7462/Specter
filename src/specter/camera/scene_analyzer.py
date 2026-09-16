"""Follows what happens in a camera's frames: tracks, fired rules and identified people."""

from collections.abc import Sequence
from dataclasses import dataclass

from specter.camera.alert_publisher import AlertPublisher
from specter.camera.person_identifier import PersonIdentifier
from specter.entities.cameras import Camera
from specter.entities.rules import Rule
from specter.entities.watchlists import Watchlist
from specter.entities.zones import Zone
from specter.storage.rules import list_camera_rules
from specter.storage.watchlists import find_watchlist
from specter.storage.zones import list_camera_zones
from specter.vision.detections import Detection
from specter.vision.frames import Frame
from specter.vision.rules import RuleEngine
from specter.vision.tracking import ObjectTracker


@dataclass(frozen=True, slots=True)
class SceneConfiguration:
    """The zones, rules and watchlists of one camera."""

    zones: tuple[Zone, ...]
    rules: tuple[Rule, ...]
    watchlists: tuple[Watchlist, ...]


def load_scene_configuration(camera: Camera) -> SceneConfiguration:
    """Reads the camera's zones, rules and watchlists from the database."""
    watchlists = (find_watchlist(watchlist_id) for watchlist_id in camera.watchlist_ids)
    return SceneConfiguration(
        zones=tuple(list_camera_zones(camera.id)),
        rules=tuple(list_camera_rules(camera.id)),
        watchlists=tuple(watchlist for watchlist in watchlists if watchlist is not None),
    )


class SceneAnalyzer:
    """Tracks a camera's detections, then fires its rules and identifies its people.

    Frames arrive in order, and each is analyzed right after its detection, while the frame still
    waits in the camera's shared memory region for identification to read.
    """

    def __init__(
        self,
        camera: Camera,
        person_identifier: PersonIdentifier,
        alert_publisher: AlertPublisher,
    ) -> None:
        self._camera = camera
        self._tracker = ObjectTracker(camera.sampling.target_fps)
        self._rule_engine = RuleEngine()
        self._person_identifier = person_identifier
        self._alert_publisher = alert_publisher

    def configure(self, configuration: SceneConfiguration) -> None:
        """Applies the camera's current zones, rules and watchlists."""
        self._rule_engine.configure(configuration.zones, configuration.rules)
        self._person_identifier.configure(configuration.watchlists)

    async def analyze(self, frame: Frame, detections: Sequence[Detection]) -> None:
        """Analyzes a frame's detections and publishes the alerts they raise."""
        detection_classes = self._camera.detection_classes
        tracking_update = self._tracker.update(
            [
                detection
                for detection in detections
                if not detection_classes or detection.object_class in detection_classes
            ],
            frame.presentation_time_seconds,
            frame.captured_at,
        )
        now_seconds = frame.presentation_time_seconds
        self._rule_engine.forget_tracks(tracking_update.ended_track_ids)
        for firing in self._rule_engine.evaluate(
            tracking_update.tracks, frame.width_pixels, frame.height_pixels, now_seconds
        ):
            await self._alert_publisher.publish_rule_firing(frame, firing)
        for match in await self._person_identifier.identify(frame, tracking_update, now_seconds):
            await self._alert_publisher.publish_match(frame, match)
