"""Cameras and how their frames are sampled."""

from dataclasses import dataclass, field, replace
from enum import StrEnum
from urllib.parse import urlsplit

from specter.core.errors import InvalidEntityError
from specter.entities.validation import (
    require_non_empty_text,
    require_positive,
    require_unique,
)

DEFAULT_TARGET_FPS = 10.0
DEFAULT_MINIMUM_FPS = 3.0


class SamplingMode(StrEnum):
    """How the rate of processed frames is chosen."""

    ADAPTIVE = "adaptive"
    FIXED = "fixed"


class TransportProtocol(StrEnum):
    """Network transport for the camera's stream."""

    TCP = "tcp"
    UDP = "udp"


class DesiredState(StrEnum):
    """Whether the owner wants the camera to run."""

    RUNNING = "running"
    STOPPED = "stopped"


class CameraStatus(StrEnum):
    """Live state of a camera process."""

    STARTING = "starting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CameraCredentials:
    """User name and password for the camera's stream."""

    username: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SamplingSettings:
    """How many frames per second the camera process runs models on."""

    mode: SamplingMode = SamplingMode.ADAPTIVE
    target_fps: float = DEFAULT_TARGET_FPS
    minimum_fps: float = DEFAULT_MINIMUM_FPS
    is_motion_gating_enabled: bool = True

    def __post_init__(self) -> None:
        require_positive(self.target_fps, "target_fps")
        require_positive(self.minimum_fps, "minimum_fps")
        if self.minimum_fps > self.target_fps:
            raise InvalidEntityError("minimum_fps must not exceed target_fps")


@dataclass(frozen=True, slots=True)
class Camera:
    """A video source whose frames are analyzed by its own camera process."""

    id: str
    owner_id: str
    name: str
    source_url: str
    credentials: CameraCredentials | None = None
    transport: TransportProtocol = TransportProtocol.TCP
    watchlist_ids: tuple[str, ...] = ()
    # An empty set means objects of every class are analyzed.
    detection_classes: frozenset[str] = frozenset()
    sampling: SamplingSettings = field(default_factory=SamplingSettings)
    is_enabled: bool = True
    desired_state: DesiredState = DesiredState.STOPPED

    def __post_init__(self) -> None:
        require_non_empty_text(self.name, "camera name")
        require_non_empty_text(self.source_url, "camera source_url")
        require_unique(self.watchlist_ids, "camera watchlist_ids")
        # A password inside the URL would be stored and returned in plain text, unlike credentials.
        if urlsplit(self.source_url).password is not None:
            raise InvalidEntityError(
                "camera source_url must not contain a password; use credentials"
            )

    @property
    def should_run(self) -> bool:
        """Whether the camera manager should keep a process running for this camera."""
        return self.is_enabled and self.desired_state is DesiredState.RUNNING

    def start(self) -> "Camera":
        """Returns a copy that the camera manager should run.

        Raises:
            InvalidEntityError: The camera is disabled.
        """
        if not self.is_enabled:
            raise InvalidEntityError("a disabled camera cannot be started")
        return replace(self, desired_state=DesiredState.RUNNING)

    def stop(self) -> "Camera":
        """Returns a copy that the camera manager should stop."""
        return replace(self, desired_state=DesiredState.STOPPED)

    def enable(self) -> "Camera":
        """Returns an enabled copy, which stays stopped until it is started."""
        return replace(self, is_enabled=True)

    def disable(self) -> "Camera":
        """Returns a disabled copy, which is also stopped."""
        return replace(self, is_enabled=False, desired_state=DesiredState.STOPPED)
