"""Live-stream configuration and health value objects."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from specter.core.errors import RuleViolation


class StreamProtocol(StrEnum):
    RTSP = "rtsp"
    RTMP = "rtmp"
    WEBRTC = "webrtc"
    HLS = "hls"
    HTTP_FLV = "http-flv"


class TransportProtocol(StrEnum):
    TCP = "tcp"
    UDP = "udp"


class StreamStatus(StrEnum):
    PROVISIONING = "provisioning"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    DEGRADED = "degraded"
    STOPPED = "stopped"
    ERROR = "error"


class DesiredState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"


class SamplingMode(StrEnum):
    ADAPTIVE = "adaptive"
    FIXED = "fixed"


@dataclass(frozen=True, slots=True)
class StreamCredentials:
    username: str
    password: str


@dataclass(frozen=True, slots=True)
class StreamSource:
    protocol: StreamProtocol
    url: str
    credentials: StreamCredentials | None = None
    transport: TransportProtocol = TransportProtocol.TCP

    def __post_init__(self) -> None:
        if not self.url.strip():
            raise RuleViolation("stream url must not be empty")


@dataclass(frozen=True, slots=True)
class SamplingConfig:
    mode: SamplingMode = SamplingMode.ADAPTIVE
    target_fps: float = 10.0
    min_fps: float = 3.0
    motion_gating: bool = True

    def __post_init__(self) -> None:
        if self.target_fps <= 0 or self.min_fps <= 0:
            raise RuleViolation("fps values must be positive")
        if self.min_fps > self.target_fps:
            raise RuleViolation("min_fps cannot exceed target_fps")


@dataclass(frozen=True, slots=True)
class RegionOfInterest:
    x: float
    y: float
    w: float
    h: float

    def __post_init__(self) -> None:
        for name, value in (("x", self.x), ("y", self.y), ("w", self.w), ("h", self.h)):
            if not 0.0 <= value <= 1.0:
                raise RuleViolation(f"roi.{name} must be normalised to [0, 1], got {value}")
        if self.w <= 0 or self.h <= 0:
            raise RuleViolation("roi must have positive size")
        if self.x + self.w > 1.0 or self.y + self.h > 1.0:
            raise RuleViolation("roi extends past the frame")


def preview_key(owner_id: str, stream_id: str, extension: str) -> str:
    """The single, overwritten-in-place blob key a running stream's latest frame is
    written to — shared by the pipeline (writer) and the `/preview` endpoint (reader)
    so the two never drift apart."""
    return f"previews/{owner_id}/{stream_id}/latest{extension}"


@dataclass(slots=True)
class StreamConfig:
    id: str
    owner_id: str
    name: str
    source: StreamSource
    camera_id: str | None = None
    watchlist_ids: list[str] = field(default_factory=list)
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    roi: list[RegionOfInterest] = field(default_factory=list)
    detect_classes: list[str] = field(default_factory=list)
    enabled: bool = True
    desired_state: DesiredState = DesiredState.STOPPED

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise RuleViolation("stream name must not be empty")

    def rename(self, name: str) -> None:
        if not name.strip():
            raise RuleViolation("stream name must not be empty")
        self.name = name

    def retarget(self, source: StreamSource) -> None:
        self.source = source

    def set_sampling(self, sampling: SamplingConfig) -> None:
        self.sampling = sampling

    def set_watchlists(self, watchlist_ids: list[str]) -> None:
        self.watchlist_ids = list(dict.fromkeys(watchlist_ids))

    def set_roi(self, roi: list[RegionOfInterest]) -> None:
        self.roi = list(roi)

    def set_detect_classes(self, classes: list[str]) -> None:
        self.detect_classes = list(dict.fromkeys(classes))

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        """Disabling also parks the desired state so the supervisor tears the run down."""
        self.enabled = False
        self.desired_state = DesiredState.STOPPED

    def start(self) -> None:
        if not self.enabled:
            raise RuleViolation("cannot start a disabled stream")
        self.desired_state = DesiredState.RUNNING

    def stop(self) -> None:
        self.desired_state = DesiredState.STOPPED

    @property
    def should_run(self) -> bool:
        return self.enabled and self.desired_state is DesiredState.RUNNING


@dataclass(frozen=True, slots=True)
class StreamHealth:
    status: StreamStatus
    fps_in: float = 0.0
    fps_processed: float = 0.0
    frames_dropped_pct: float = 0.0
    last_frame_at: datetime | None = None
    reconnect_count: int = 0
    inference_p95_ms: float = 0.0
    queue_depth: dict[str, int] = field(default_factory=dict)
    last_error: str | None = None
