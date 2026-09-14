"""Everything a stream pipeline needs, and the knobs that shape it.

``PipelineDeps`` is the port bundle the ``specter-ingest`` supervisor builds once and
hands to every ``run_stream`` call. ``PipelineTuning`` holds the numbers that are policy,
not wiring — safe to expose through settings later.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from specter.application.ports import (
    BlobStore,
    Detector,
    Embedder,
    EventBus,
    FrameCodec,
    FrameSourceFactory,
    HealthStore,
    Tracker,
    UnitOfWorkFactory,
    VectorIndex,
)
from specter.core.clock import Clock


@dataclass(frozen=True, slots=True)
class PipelineTuning:
    queue_size: int = 8
    """Frames buffered between decode and inference; the sampler sheds the overflow."""

    directory_refresh_s: float = 30.0
    """How often the resolved watchlist/target snapshot is rebuilt from the database."""

    top_k: int = 5
    min_detection_confidence: float = 0.5
    default_threshold: float = 0.78
    """Used only when a candidate's watchlist can't be resolved (should not happen)."""

    need: int = 3
    window: int = 5
    ema_alpha: float = 0.4
    cooldown_s: float = 45.0

    motion_min_delta: float = 2.0
    """Mean absolute 8-bit pixel change below which a frame counts as 'no motion'."""

    capture_evidence: bool = True
    evidence_ttl_s: int = 3600

    health_publish_interval_s: float = 2.0
    """How often a stream writes its health snapshot to the shared KV — also the cadence
    for noticing a watchlist edit and for the AIMD sampling-rate adjustment."""

    health_ttl_s: int = 10
    """TTL on that snapshot — a stalled/crashed stream's health naturally expires."""

    aimd_decrease_factor: float = 0.5
    """Multiplicative backoff on the sampler's effective fps when inference p95 exceeds
    budget at the current rate."""

    aimd_increase_fps: float = 0.5
    """Additive recovery step, per adjustment, back toward target_fps."""


@dataclass(frozen=True, slots=True)
class PipelineDeps:
    uow_factory: UnitOfWorkFactory
    frame_source_factory: FrameSourceFactory
    detector: Detector
    tracker: Tracker
    embedders: Mapping[str, Embedder]
    vectors: VectorIndex
    blob: BlobStore
    bus: EventBus
    health: HealthStore
    clock: Clock
    codec: FrameCodec
    tuning: PipelineTuning = field(default_factory=PipelineTuning)
