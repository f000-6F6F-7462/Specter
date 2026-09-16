"""Exports and downloads every model of Specter's model manifest into a models directory.

Runs inside the container built from this directory, where torch, Ultralytics and pnnx are
installed, so none of them reaches a device. Start it with ``make models``.
"""

import argparse
import hashlib
import importlib.util
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import httpx
import yaml

DOWNLOAD_TIMEOUT_SECONDS = 300.0
ONNX_OPSET_VERSION = 17
# Sample images from the model sources themselves, used by the tests of the model wrappers.
TEST_IMAGE_URLS = {
    "test_images/bus.jpg": "https://github.com/ultralytics/assets/releases/download/v0.0.0/bus.jpg",
    "test_images/faces.jpg": (
        "https://raw.githubusercontent.com/deepinsight/insightface/master/"
        "python-package/insightface/data/images/t1.jpg"
    ),
}


def main() -> None:
    """Parses the command line and produces every model that is missing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true", help="export models that already exist")
    arguments = parser.parse_args()

    models: dict[str, dict[str, Any]] = yaml.safe_load(arguments.manifest.read_text())["models"]
    arguments.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work_directory:
        produced_model_ids: set[str] = set()
        for model_id in models:
            produce_model(
                model_id,
                models,
                output_directory=arguments.output,
                work_directory=Path(work_directory),
                produced_model_ids=produced_model_ids,
                is_forced=arguments.force,
            )
    for image_path, image_url in TEST_IMAGE_URLS.items():
        test_image = arguments.output / image_path
        if not test_image.exists():
            test_image.parent.mkdir(parents=True, exist_ok=True)
            download_file(image_url, test_image)
    for model_id, model in models.items():
        for model_file in model["files"]:
            stored_file = arguments.output / model_file["path"]
            print(f"{model_id}: {model_file['path']} sha256={compute_sha256(stored_file)}")


def produce_model(
    model_id: str,
    models: dict[str, dict[str, Any]],
    *,
    output_directory: Path,
    work_directory: Path,
    produced_model_ids: set[str],
    is_forced: bool,
) -> None:
    """Downloads or exports one model, after the model it is converted from."""
    if model_id in produced_model_ids:
        return
    model = models[model_id]
    stored_files = [output_directory / model_file["path"] for model_file in model["files"]]
    if not is_forced and all(stored_file.exists() for stored_file in stored_files):
        produced_model_ids.add(model_id)
        return

    print(f"producing {model_id}")
    export = model.get("export")
    if export is None:
        download_model_files(model, output_directory, work_directory)
    elif export["kind"] == "ultralytics":
        export_with_ultralytics(model, stored_files, work_directory)
    elif export["kind"] == "osnet":
        export_osnet(model, stored_files, work_directory)
    elif export["kind"] == "pnnx":
        produce_model(
            export["source_model"],
            models,
            output_directory=output_directory,
            work_directory=work_directory,
            produced_model_ids=produced_model_ids,
            is_forced=False,
        )
        source_model = models[export["source_model"]]
        source_file = output_directory / source_model["files"][0]["path"]
        convert_with_pnnx(model, source_file, stored_files, work_directory)
    else:
        raise ValueError(f"model {model_id} has unknown export kind {export['kind']}")
    produced_model_ids.add(model_id)


def download_model_files(
    model: dict[str, Any], output_directory: Path, work_directory: Path
) -> None:
    """Extracts each published file of the model from its archive and checks its checksum."""
    for model_file in model["files"]:
        download = model_file["download"]
        archive_file = work_directory / Path(download["archive_url"]).name
        if not archive_file.exists():
            download_file(download["archive_url"], archive_file)
        stored_file = output_directory / model_file["path"]
        stored_file.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_file) as archive:
            stored_file.write_bytes(archive.read(download["archive_member"]))
        if compute_sha256(stored_file) != model_file["sha256"]:
            stored_file.unlink()
            raise ValueError(f"{stored_file} fails its checksum")


def export_with_ultralytics(
    model: dict[str, Any], stored_files: list[Path], work_directory: Path
) -> None:
    """Exports Ultralytics weights at the model's input size, keeping its end-to-end boxes."""
    from ultralytics import YOLO  # noqa: PLC0415

    weights_file = work_directory / model["export"]["weights"]
    # Ultralytics downloads missing official weights to the path it is given.
    network = YOLO(str(weights_file))
    exported_path = Path(
        network.export(
            format=model["format"],
            imgsz=(model["input_height"], model["input_width"]),
            half=False,
            # A dynamic batch lets the detector run several cameras' frames in one call; NCNN
            # runs one image at a time and needs fixed shapes.
            dynamic=model["format"] == "onnx",
        )
    )
    if model["format"] == "onnx":
        copy_file(exported_path, stored_files[0])
        return
    for stored_file in stored_files:
        # The NCNN export is a directory holding model.ncnn.param and model.ncnn.bin.
        suffix = stored_file.suffix
        copy_file(exported_path / f"model.ncnn{suffix}", stored_file)


def export_osnet(model: dict[str, Any], stored_files: list[Path], work_directory: Path) -> None:
    """Exports OSNet's feature extractor, whose output is the appearance embedding."""
    import torch  # noqa: PLC0415

    export = model["export"]
    definition_file = work_directory / "osnet_definition.py"
    download_file(export["definition_url"], definition_file)
    weights_file = work_directory / f"{export['variant']}.pth"
    download_file(export["weights_url"], weights_file)

    specification = importlib.util.spec_from_file_location("osnet_definition", definition_file)
    if specification is None or specification.loader is None:
        raise ValueError(f"cannot load {definition_file}")
    definition = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(definition)
    network = getattr(definition, export["variant"])(num_classes=1, pretrained=False)

    checkpoint = torch.load(weights_file, map_location="cpu", weights_only=False)
    state = checkpoint.get("state_dict", checkpoint)
    # The classifier was trained for the dataset's identities and is not part of the embedding.
    feature_state = {
        name.removeprefix("module."): value
        for name, value in state.items()
        if not name.removeprefix("module.").startswith("classifier.")
    }
    missing_names, unexpected_names = network.load_state_dict(feature_state, strict=False)
    if unexpected_names or any(not name.startswith("classifier.") for name in missing_names):
        raise ValueError(f"OSNet weights do not match: {missing_names=} {unexpected_names=}")

    network.eval()
    example_input = torch.zeros(1, 3, model["input_height"], model["input_width"])
    torch.onnx.export(
        network,
        (example_input,),
        str(stored_files[0]),
        input_names=["images"],
        output_names=["embeddings"],
        dynamic_axes={"images": {0: "batch"}, "embeddings": {0: "batch"}},
        opset_version=ONNX_OPSET_VERSION,
        dynamo=False,
    )


def convert_with_pnnx(
    model: dict[str, Any], source_file: Path, stored_files: list[Path], work_directory: Path
) -> None:
    """Converts an ONNX model to NCNN at the model's fixed input size."""
    conversion_directory = work_directory / f"pnnx_{source_file.stem}"
    conversion_directory.mkdir(exist_ok=True)
    working_copy = conversion_directory / source_file.name
    shutil.copyfile(source_file, working_copy)
    input_shape = f"[1,3,{model['input_height']},{model['input_width']}]"
    subprocess.run(  # noqa: S603
        ["pnnx", working_copy.name, f"inputshape={input_shape}"],  # noqa: S607
        cwd=conversion_directory,
        check=True,
    )
    for stored_file in stored_files:
        copy_file(
            conversion_directory / f"{working_copy.stem}.ncnn{stored_file.suffix}", stored_file
        )


def download_file(url: str, destination: Path) -> None:
    """Downloads the URL to the destination file."""
    with httpx.stream(
        "GET", url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_SECONDS
    ) as response:
        response.raise_for_status()
        with destination.open("wb") as stream:
            for chunk in response.iter_bytes():
                stream.write(chunk)


def copy_file(source: Path, destination: Path) -> None:
    """Copies a file, creating the destination's directory."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def compute_sha256(file: Path) -> str:
    """Returns the file's SHA-256 as hexadecimal text."""
    return hashlib.sha256(file.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
