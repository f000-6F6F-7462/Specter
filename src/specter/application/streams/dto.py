"""Request and view objects for the stream-management use cases.

The HTTP layer maps its Pydantic bodies to these and these to its responses; the use
cases never see a framework type.
"""

from dataclasses import dataclass, field

from specter.domain.streams import (
    DesiredState,
    RegionOfInterest,
    SamplingConfig,
    SamplingMode,
    StreamConfig,
    StreamCredentials,
    StreamProtocol,
    StreamSource,
    StreamStatus,
    TransportProtocol,
)


@dataclass(frozen=True, slots=True)
class SourceSpec:
    protocol: StreamProtocol
    url: str
    username: str | None = None
    password: str | None = None
    transport: TransportProtocol = TransportProtocol.TCP

    def to_domain(self) -> StreamSource:
        creds = (
            StreamCredentials(self.username, self.password)
            if self.username is not None and self.password is not None
            else None
        )
        return StreamSource(
            protocol=self.protocol,
            url=self.url,
            credentials=creds,
            transport=self.transport,
        )


@dataclass(frozen=True, slots=True)
class SamplingSpec:
    mode: SamplingMode = SamplingMode.ADAPTIVE
    target_fps: float = 10.0
    min_fps: float = 3.0
    motion_gating: bool = True

    def to_domain(self) -> SamplingConfig:
        return SamplingConfig(
            mode=self.mode,
            target_fps=self.target_fps,
            min_fps=self.min_fps,
            motion_gating=self.motion_gating,
        )


@dataclass(frozen=True, slots=True)
class RoiSpec:
    x: float
    y: float
    w: float
    h: float

    def to_domain(self) -> RegionOfInterest:
        return RegionOfInterest(x=self.x, y=self.y, w=self.w, h=self.h)


@dataclass(frozen=True, slots=True)
class CreateStreamRequest:
    owner_id: str
    name: str
    source: SourceSpec
    camera_id: str | None = None
    watchlist_ids: tuple[str, ...] = ()
    sampling: SamplingSpec = field(default_factory=SamplingSpec)
    roi: tuple[RoiSpec, ...] = ()
    detect_classes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UpdateStreamRequest:
    name: str | None = None
    source: SourceSpec | None = None
    camera_id: str | None = None
    watchlist_ids: tuple[str, ...] | None = None
    sampling: SamplingSpec | None = None
    roi: tuple[RoiSpec, ...] | None = None
    detect_classes: tuple[str, ...] | None = None
    enabled: bool | None = None


@dataclass(frozen=True, slots=True)
class SamplingView:
    mode: SamplingMode
    target_fps: float
    min_fps: float
    motion_gating: bool

    @classmethod
    def of(cls, sampling: SamplingConfig) -> "SamplingView":
        return cls(
            mode=sampling.mode,
            target_fps=sampling.target_fps,
            min_fps=sampling.min_fps,
            motion_gating=sampling.motion_gating,
        )


@dataclass(frozen=True, slots=True)
class RoiView:
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True, slots=True)
class StreamView:
    id: str
    owner_id: str
    name: str
    protocol: StreamProtocol
    url: str
    transport: TransportProtocol
    has_credentials: bool
    camera_id: str | None
    watchlist_ids: tuple[str, ...]
    sampling: SamplingView
    roi: tuple[RoiView, ...]
    detect_classes: tuple[str, ...]
    enabled: bool
    desired_state: DesiredState
    live_status: StreamStatus

    @classmethod
    def of(
        cls, stream: StreamConfig, *, live_status: StreamStatus = StreamStatus.STOPPED
    ) -> "StreamView":
        return cls(
            id=stream.id,
            owner_id=stream.owner_id,
            name=stream.name,
            protocol=stream.source.protocol,
            url=stream.source.url,
            transport=stream.source.transport,
            has_credentials=stream.source.credentials is not None,
            camera_id=stream.camera_id,
            watchlist_ids=tuple(stream.watchlist_ids),
            sampling=SamplingView.of(stream.sampling),
            roi=tuple(RoiView(r.x, r.y, r.w, r.h) for r in stream.roi),
            detect_classes=tuple(stream.detect_classes),
            enabled=stream.enabled,
            desired_state=stream.desired_state,
            live_status=live_status,
        )
