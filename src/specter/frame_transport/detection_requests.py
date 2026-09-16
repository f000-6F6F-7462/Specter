"""The request a camera process sends to the detector for one frame, and the detector's reply.

These messages stay inside the device, so they are not part of the published contract.
"""

from pydantic import BaseModel, ConfigDict, Field

DETECTION_REQUESTS_SUBJECT = "specter.detector.object_detection"
# Every detector process joins this queue group, so each request reaches exactly one of them.
DETECTOR_QUEUE_GROUP = "detectors"


class _RequestPart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DetectionRequest(_RequestPart):
    """A frame waiting in a camera's shared memory region, letterboxed to the model's input."""

    camera_id: str
    shared_memory_name: str
    frame_sequence_number: int = Field(ge=0)
    input_width: int = Field(gt=0)
    input_height: int = Field(gt=0)
    # How the camera fitted its frame into the input, to map boxes back to the frame.
    scale: float = Field(gt=0)
    padding_x_pixels: int = Field(ge=0)
    padding_y_pixels: int = Field(ge=0)
    frame_width_pixels: int = Field(gt=0)
    frame_height_pixels: int = Field(gt=0)


class DetectedObject(_RequestPart):
    """An object the detector found, in the frame's pixels."""

    object_class: str
    confidence_ratio: float = Field(ge=0.0, le=1.0)
    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class DetectionReply(_RequestPart):
    """The objects found in one frame."""

    detected_objects: tuple[DetectedObject, ...]
