"""Turns person crops into OSNet appearance embeddings for re-identification."""

from collections.abc import Sequence

import cv2
import numpy as np

from specter.inference.backends import Embedding, Tensor, normalize_embedding
from specter.vision.frames import FrameImage

MAXIMUM_PIXEL_INTENSITY = 255.0
# OSNet was trained on ImageNet-normalized RGB images.
IMAGENET_MEAN_RATIOS = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STANDARD_DEVIATION_RATIOS = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class AppearanceEmbedder:
    """OSNet's input preparation and embedding output, for one model input size."""

    def __init__(self, input_width: int, input_height: int) -> None:
        self._input_width = input_width
        self._input_height = input_height

    def build_input_tensor(self, crops: Sequence[FrameImage]) -> Tensor:
        """Resizes BGR crops to the model's input and stacks them as normalized RGB."""
        resized_crops = [
            cv2.resize(
                crop, (self._input_width, self._input_height), interpolation=cv2.INTER_LINEAR
            )
            for crop in crops
        ]
        rgb_batch = np.stack(resized_crops)[..., ::-1].astype(np.float32) / MAXIMUM_PIXEL_INTENSITY
        normalized_batch = (rgb_batch - IMAGENET_MEAN_RATIOS) / IMAGENET_STANDARD_DEVIATION_RATIOS
        return np.ascontiguousarray(normalized_batch.transpose(0, 3, 1, 2), dtype=np.float32)

    @staticmethod
    def read_embedding(outputs: Sequence[Tensor]) -> Embedding | None:
        """Returns one crop's unit-length embedding, or None when the output has no direction."""
        return normalize_embedding(outputs[0])
