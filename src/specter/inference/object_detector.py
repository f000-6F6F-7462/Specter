"""Finds objects with YOLO26: preparing frames for the model and reading its boxes back."""

from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from specter.entities.geometry import BoundingBox
from specter.inference.backends import Tensor
from specter.vision.detections import Detection
from specter.vision.frames import FrameImage

# The 80 COCO classes, in the order of the model's class scores.
COCO_CLASS_NAMES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush",
)  # fmt: skip
LETTERBOX_PADDING_INTENSITY = 114
MAXIMUM_PIXEL_INTENSITY = 255.0
BOX_VALUE_COUNT = 4
DEFAULT_CONFIDENCE_THRESHOLD_RATIO = 0.25
DEFAULT_OVERLAP_THRESHOLD_RATIO = 0.45


@dataclass(frozen=True, slots=True)
class LetterboxTransform:
    """How a frame was scaled and padded into the model's input, to map boxes back to the frame."""

    scale: float
    padding_x_pixels: int
    padding_y_pixels: int
    frame_width_pixels: int
    frame_height_pixels: int


class ObjectDetector:
    """YOLO26's input preparation and output decoding, for one model input size.

    Frames are letterboxed in the camera process, which also makes every frame the same size for
    batching; the batch tensor is built and decoded in the detector process.
    """

    def __init__(
        self,
        input_width: int,
        input_height: int,
        *,
        confidence_threshold_ratio: float = DEFAULT_CONFIDENCE_THRESHOLD_RATIO,
        overlap_threshold_ratio: float = DEFAULT_OVERLAP_THRESHOLD_RATIO,
        class_names: Sequence[str] = COCO_CLASS_NAMES,
    ) -> None:
        self._input_width = input_width
        self._input_height = input_height
        self._confidence_threshold_ratio = confidence_threshold_ratio
        self._overlap_threshold_ratio = overlap_threshold_ratio
        self._class_names = tuple(class_names)

    def letterbox(self, image: FrameImage) -> tuple[FrameImage, LetterboxTransform]:
        """Scales the frame to fit the model's input without distortion and pads the rest."""
        frame_height, frame_width = image.shape[:2]
        scale = min(self._input_width / frame_width, self._input_height / frame_height)
        scaled_width = round(frame_width * scale)
        scaled_height = round(frame_height * scale)
        padding_x = (self._input_width - scaled_width) // 2
        padding_y = (self._input_height - scaled_height) // 2
        letterboxed_image = np.full(
            (self._input_height, self._input_width, 3), LETTERBOX_PADDING_INTENSITY, dtype=np.uint8
        )
        letterboxed_image[
            padding_y : padding_y + scaled_height, padding_x : padding_x + scaled_width
        ] = cv2.resize(image, (scaled_width, scaled_height), interpolation=cv2.INTER_LINEAR)
        transform = LetterboxTransform(
            scale=scale,
            padding_x_pixels=padding_x,
            padding_y_pixels=padding_y,
            frame_width_pixels=frame_width,
            frame_height_pixels=frame_height,
        )
        return letterboxed_image, transform

    @staticmethod
    def build_input_tensor(letterboxed_images: Sequence[FrameImage]) -> Tensor:
        """Stacks letterboxed BGR frames into the model's RGB input, scaled from 0 to 1."""
        rgb_batch = np.stack(letterboxed_images)[..., ::-1].transpose(0, 3, 1, 2)
        return np.ascontiguousarray(rgb_batch, dtype=np.float32) / np.float32(
            MAXIMUM_PIXEL_INTENSITY
        )

    def decode(self, outputs: Sequence[Tensor], transform: LetterboxTransform) -> list[Detection]:
        """Returns the objects above the confidence threshold in frame pixels, after NMS."""
        # Each column holds one candidate's center, size and a score for every class.
        candidates = outputs[0].reshape(BOX_VALUE_COUNT + len(self._class_names), -1).T
        class_scores = candidates[:, BOX_VALUE_COUNT:]
        class_indices = class_scores.argmax(axis=1)
        confidences = class_scores[np.arange(len(candidates)), class_indices]
        is_confident = confidences >= self._confidence_threshold_ratio
        if not is_confident.any():
            return []
        centers_x, centers_y, widths, heights = candidates[is_confident, :BOX_VALUE_COUNT].T
        boxes = np.stack((centers_x - widths / 2, centers_y - heights / 2, widths, heights), axis=1)
        kept_indices = cv2.dnn.NMSBoxesBatched(
            boxes.tolist(),
            confidences[is_confident].tolist(),
            class_indices[is_confident].tolist(),
            self._confidence_threshold_ratio,
            self._overlap_threshold_ratio,
        )
        return [
            Detection(
                object_class=self._class_names[class_indices[is_confident][index]],
                confidence_ratio=min(float(confidences[is_confident][index]), 1.0),
                bounding_box=self._map_to_frame(boxes[index], transform),
            )
            for index in np.asarray(kept_indices, dtype=np.int64).reshape(-1)
        ]

    @staticmethod
    def _map_to_frame(box: Tensor, transform: LetterboxTransform) -> BoundingBox:
        left = (float(box[0]) - transform.padding_x_pixels) / transform.scale
        top = (float(box[1]) - transform.padding_y_pixels) / transform.scale
        width = float(box[2]) / transform.scale
        height = float(box[3]) / transform.scale
        return BoundingBox(
            x=round(left), y=round(top), width=max(round(width), 1), height=max(round(height), 1)
        ).clip_to_frame(transform.frame_width_pixels, transform.frame_height_pixels)
