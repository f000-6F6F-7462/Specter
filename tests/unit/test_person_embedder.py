"""PersonEmbedder — validation and a real load->preprocess->infer->normalize round
trip against a freshly-built OSNet's own state dict (a real architecture, standing in
for a real pretrained checkpoint — this proves the pipeline is wired correctly, not
that the embeddings are recognition-accurate, which needs real trained weights)."""

import numpy as np
import pytest

from specter.core.errors import ConfigurationError, DependencyFailure
from specter.domain.vision import Crop
from specter.infrastructure.ml.person_embedder import PersonEmbedder, _load_osnet_module


def test_empty_weights_path_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        PersonEmbedder(weights="")


def test_missing_weights_file_is_a_configuration_error(tmp_path) -> None:
    embedder = PersonEmbedder(weights=str(tmp_path / "nope.pth"))
    with pytest.raises(ConfigurationError):
        embedder.embed_sync([np.zeros((64, 32, 3), dtype=np.uint8)])


def test_unknown_variant_raises(tmp_path) -> None:
    weights = tmp_path / "w.pth"
    weights.write_bytes(b"not-a-real-checkpoint-but-path-must-exist")
    embedder = PersonEmbedder(weights=str(weights), variant="osnet_bogus")
    with pytest.raises(ConfigurationError):
        embedder.embed_sync([np.zeros((64, 32, 3), dtype=np.uint8)])


@pytest.fixture(scope="module")
def real_weights(tmp_path_factory) -> str:
    """A real OSNet's own random state dict, saved as a local checkpoint — exercises
    the actual weight-loading code path (key stripping, shape matching) against a
    genuine architecture, without needing a real (large, Google-Drive-hosted) download."""
    import torch

    osnet = _load_osnet_module()
    model = osnet.osnet_x1_0(num_classes=1000, pretrained=False)
    path = tmp_path_factory.mktemp("osnet") / "weights.pth"
    torch.save(model.state_dict(), path)
    return str(path)


class TestRealRoundTrip:
    async def test_produces_unit_norm_512d_embeddings(self, real_weights: str) -> None:
        embedder = PersonEmbedder(weights=real_weights, device="cpu")
        crop = Crop(
            stream_id="s",
            track_id=1,
            modality="person",
            image=np.random.default_rng(0).integers(0, 255, (180, 90, 3), dtype=np.uint8),
            quality=1.0,
        )

        [embedding] = await embedder.embed([crop])

        assert embedding.modality == "person"
        assert embedding.vector.shape == (512,)
        assert embedding.vector.dtype == np.float32
        assert float(np.linalg.norm(embedding.vector)) == pytest.approx(1.0, abs=1e-4)

    async def test_embedding_is_deterministic_for_the_same_crop(self, real_weights: str) -> None:
        embedder = PersonEmbedder(weights=real_weights, device="cpu")
        image = np.random.default_rng(1).integers(0, 255, (200, 100, 3), dtype=np.uint8)
        crop = Crop(stream_id="s", track_id=1, modality="person", image=image, quality=1.0)

        [first] = await embedder.embed([crop])
        [second] = await embedder.embed([crop])

        assert np.allclose(first.vector, second.vector)

    async def test_model_loads_once_and_is_reused(self, real_weights: str) -> None:
        embedder = PersonEmbedder(weights=real_weights, device="cpu")
        crop = Crop(
            stream_id="s",
            track_id=1,
            modality="person",
            image=np.zeros((100, 50, 3), dtype=np.uint8),
            quality=1.0,
        )
        await embedder.embed([crop])
        model_after_first = embedder._model  # noqa: SLF001
        await embedder.embed([crop])
        assert embedder._model is model_after_first  # noqa: SLF001

    async def test_empty_crops_returns_empty(self, real_weights: str) -> None:
        embedder = PersonEmbedder(weights=real_weights, device="cpu")
        assert await embedder.embed([]) == []
