"""Person Re-ID embedder: OSNet — the ``person`` counterpart to ``FaceEmbedder``'s
``face`` modality, so a target's appearance (not just their face) can be matched
across cameras/angles where no face is visible.

The ``osnet.py`` model *definition* is loaded directly from the installed
``torchreid`` package's file — bypassing its own top-level ``__init__.py``, which
eagerly imports its entire training framework (dataset downloaders, training
engines, loss functions — none of which pure inference needs, and some of which
pull in extra packages this service has no other use for). ``osnet.py`` itself only
imports ``torch``, so once loaded this way the only runtime dependency is ``torch``.

Weights: a local ``.pth`` state dict in the standard torchreid checkpoint format.
Official pretrained weights are published via Google Drive (no stable direct-download
URL) — download one manually and point ``weights`` at the local file, the same
"operator supplies local weights" convention as ``YoloDetector`` (``yolo11m.pt``) and
``InsightFaceEmbeddingService`` (the buffalo_l pack). No auto-download here.
"""

import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any

import numpy as np

from specter.core.errors import ConfigurationError, DependencyFailure
from specter.domain.vision import Crop, Embedding, Vector

_INPUT_HW = (256, 128)  # the standard Re-ID input size (height, width)
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class PersonEmbedder:
    modality = "person"

    def __init__(self, *, weights: str, variant: str = "osnet_x1_0", device: str = "cpu") -> None:
        if not weights:
            raise ConfigurationError("PersonEmbedder needs a local OSNet .pth weights path")
        self._weights = weights
        self._variant = variant
        self._device = device
        self._model: Any | None = None

    @property
    def model_version(self) -> str:
        return self._variant

    async def embed(self, crops: Sequence[Crop]) -> list[Embedding]:
        if not crops:
            return []
        vectors = await asyncio.to_thread(self.embed_sync, [crop.image for crop in crops])
        return [Embedding(modality=self.modality, vector=vector) for vector in vectors]

    def _load(self) -> Any:
        if self._model is None:
            osnet = _load_osnet_module()
            builder = getattr(osnet, self._variant, None)
            if builder is None:
                raise ConfigurationError(f"unknown OSNet variant {self._variant!r}")
            # osnet is a dynamically loaded module (see _load_osnet_module): pylint
            # can't infer that the None case above already ruled out anything but the
            # real model-builder function here.
            model = builder(num_classes=1000, pretrained=False)  # pylint: disable=not-callable
            _load_local_weights(model, self._weights)
            model.eval()
            model.to(self._device)
            self._model = model
        return self._model

    def embed_sync(self, images: list[np.ndarray]) -> list[Vector]:
        """Synchronous batch embed — the seam ``BatchedEmbedder``'s ``MicroBatcher``
        calls directly (via ``asyncio.to_thread``) to coalesce crops from every
        concurrently-running stream into one call."""
        import cv2  # pylint: disable=import-outside-toplevel
        import torch  # pylint: disable=import-outside-toplevel

        model = self._load()
        height, width = _INPUT_HW
        batch = np.empty((len(images), 3, height, width), dtype=np.float32)
        for i, image in enumerate(images):
            resized = cv2.resize(image, (width, height), interpolation=cv2.INTER_LINEAR)
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            rgb = (rgb - _IMAGENET_MEAN) / _IMAGENET_STD
            batch[i] = rgb.transpose(2, 0, 1)
        with torch.no_grad():
            tensor = torch.from_numpy(batch).to(self._device)
            features = torch.nn.functional.normalize(model(tensor), dim=1)
        return [vec.detach().cpu().numpy().astype(np.float32) for vec in features]


def _load_osnet_module() -> Any:
    import importlib.util  # pylint: disable=import-outside-toplevel
    import os  # pylint: disable=import-outside-toplevel

    spec = importlib.util.find_spec("torchreid")
    if spec is None or not spec.submodule_search_locations:
        raise DependencyFailure(
            "PersonEmbedder needs the 'torchreid' package installed (`pip install "
            "torchreid`, the [ml] extra) — only its osnet.py model file is read; its "
            "own (broken-without-extra-deps) __init__ is never imported."
        )
    osnet_path = os.path.join(spec.submodule_search_locations[0], "reid", "models", "osnet.py")
    module_spec = importlib.util.spec_from_file_location("_specter_osnet", osnet_path)
    assert module_spec is not None and module_spec.loader is not None  # noqa: S101
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def _load_local_weights(model: Any, path: str) -> None:
    import torch  # pylint: disable=import-outside-toplevel

    try:
        state_dict = torch.load(path, map_location="cpu", weights_only=True)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"OSNet weights not found: {path!r}") from exc
    model_dict = model.state_dict()
    matched: OrderedDict[str, Any] = OrderedDict()
    for key, value in state_dict.items():
        key = key[7:] if key.startswith("module.") else key  # strip DataParallel prefix
        if key in model_dict and model_dict[key].shape == value.shape:
            matched[key] = value
    if not matched:
        raise DependencyFailure(f"no layers in {path!r} matched the OSNet architecture")
    model_dict.update(matched)
    model.load_state_dict(model_dict)
