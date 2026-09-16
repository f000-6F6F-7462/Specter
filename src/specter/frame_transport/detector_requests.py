"""The requests a camera process sends to the detector for one frame, and the detector's replies.

Both requests point at the frame in the camera's shared memory region instead of carrying it. These
messages stay inside the device, so they are not part of the published contract.
"""

from pydantic import BaseModel, ConfigDict, Field

DETECTION_REQUESTS_SUBJECT = "specter.detector.object_detection"
IDENTIFICATION_REQUESTS_SUBJECT = "specter.detector.identification"
# Every detector process joins this queue group, so each request reaches exactly one of them.
DETECTOR_QUEUE_GROUP = "detectors"


class _RequestPart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SharedFrameReference(_RequestPart):
    """A frame waiting in a camera's shared memory region."""

    camera_id: str
    shared_memory_name: str
    frame_sequence_number: int = Field(ge=0)
    frame_width_pixels: int = Field(gt=0)
    frame_height_pixels: int = Field(gt=0)


class PixelBox(_RequestPart):
    """A box in the frame's pixels, anchored at its top-left corner."""

    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class DetectionRequest(_RequestPart):
    """Asks for the objects in a frame."""

    frame: SharedFrameReference


class DetectedObject(_RequestPart):
    """An object the detector found, in the frame's pixels."""

    object_class: str
    confidence_ratio: float = Field(ge=0.0, le=1.0)
    box: PixelBox


class DetectionReply(_RequestPart):
    """The objects found in one frame."""

    detected_objects: tuple[DetectedObject, ...]


class IdentificationTask(_RequestPart):
    """What to embed for one tracked person."""

    track_id: int = Field(ge=0)
    person_box: PixelBox
    # The face is embedded only when its quality score beats this; None skips the face.
    minimum_face_quality_score_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    embeds_appearance: bool = False


class IdentificationRequest(_RequestPart):
    """Asks for the face and appearance embeddings of tracked people in a frame."""

    frame: SharedFrameReference
    tasks: tuple[IdentificationTask, ...]


class FaceSample(_RequestPart):
    """The best face found inside a person's box."""

    quality_score_ratio: float = Field(ge=0.0, le=1.0)
    has_passed_quality_gate: bool
    # Present only when the face passed the gate and beat the requested quality score.
    embedding: tuple[float, ...] | None = None


class IdentificationResult(_RequestPart):
    """The embeddings of one tracked person."""

    track_id: int = Field(ge=0)
    # None when the task skipped the face or no face was found in the person's box.
    face: FaceSample | None = None
    appearance_embedding: tuple[float, ...] | None = None


class IdentificationReply(_RequestPart):
    """The embeddings of every task; empty when the frame was already replaced."""

    results: tuple[IdentificationResult, ...]
