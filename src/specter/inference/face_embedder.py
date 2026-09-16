"""Turns faces into ArcFace embeddings, after aligning them the way ArcFace was trained."""

from collections.abc import Sequence

import cv2
import numpy as np

from specter.inference.backends import Embedding, Tensor, normalize_embedding
from specter.vision.detections import FaceDetection
from specter.vision.frames import FrameImage

ALIGNED_FACE_SIZE_PIXELS = 112
# Where ArcFace expects the five landmarks of a 112-pixel face, as published by InsightFace.
REFERENCE_LANDMARKS = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)
INPUT_MEAN_INTENSITY = 127.5
INPUT_STANDARD_DEVIATION_INTENSITY = 127.5


class FaceEmbedder:
    """ArcFace's face alignment, input preparation and embedding output."""

    @staticmethod
    def align(image: FrameImage, face: FaceDetection) -> FrameImage:
        """Rotates, scales and crops the face so that its landmarks land where ArcFace expects.

        Raises:
            ValueError: The landmarks do not define a usable transform.
        """
        landmarks = np.array(face.landmarks, dtype=np.float32)
        transform, _ = cv2.estimateAffinePartial2D(landmarks, REFERENCE_LANDMARKS, method=cv2.LMEDS)
        if transform is None:
            raise ValueError("face landmarks do not define an alignment")
        aligned_face = cv2.warpAffine(
            image, transform, (ALIGNED_FACE_SIZE_PIXELS, ALIGNED_FACE_SIZE_PIXELS)
        )
        return np.asarray(aligned_face, dtype=np.uint8)

    @staticmethod
    def build_input_tensor(aligned_faces: Sequence[FrameImage]) -> Tensor:
        """Stacks aligned BGR faces into the model's normalized RGB input."""
        rgb_batch = np.stack(aligned_faces)[..., ::-1].astype(np.float32)
        normalized_batch = (rgb_batch - INPUT_MEAN_INTENSITY) / INPUT_STANDARD_DEVIATION_INTENSITY
        return np.ascontiguousarray(normalized_batch.transpose(0, 3, 1, 2), dtype=np.float32)

    @staticmethod
    def read_embedding(outputs: Sequence[Tensor]) -> Embedding | None:
        """Returns one face's unit-length embedding, or None when the output has no direction."""
        return normalize_embedding(outputs[0])
