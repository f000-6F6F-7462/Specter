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
