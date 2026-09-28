"""Reference image files on the device's disk."""

import shutil
from pathlib import Path, PurePosixPath

from specter.core.errors import NotFoundError

REFERENCE_IMAGES_DIRECTORY_NAME = "reference_images"
PARTIAL_FILE_SUFFIX = ".partial"
FORBIDDEN_PATH_COMPONENTS = frozenset({"", ".", ".."})


class ReferenceImageStore:
    """Keeps uploaded reference images under the data directory, one directory per target.

    Grouping by owner and target lets a deleted target or owner take all its images with it.
    """

    def __init__(self, data_directory: Path) -> None:
        self._data_directory = data_directory
        self._images_directory = data_directory / REFERENCE_IMAGES_DIRECTORY_NAME

    def build_image_path(
        self, owner_id: str, target_id: str, image_id: str, file_suffix: str
    ) -> str:
        """Returns where a reference image belongs, relative to the data directory.

        Raises:
            ValueError: An id could escape the reference images directory.
        """
        for identifier in (owner_id, target_id, image_id):
            _require_path_component(identifier)
        return str(
            PurePosixPath(
                REFERENCE_IMAGES_DIRECTORY_NAME, owner_id, target_id, f"{image_id}{file_suffix}"
            )
        )

    def write_image(self, image_path: str, image_bytes: bytes) -> None:
        """Writes the image through a temporary file, so a crash never leaves half an image."""
        image_file = self._data_directory / image_path
        image_file.parent.mkdir(parents=True, exist_ok=True)
        partial_file = image_file.with_name(image_file.name + PARTIAL_FILE_SUFFIX)
        partial_file.write_bytes(image_bytes)
        partial_file.replace(image_file)

    def resolve_image_file(self, image_path: str) -> Path:
        """Returns the absolute file of a stored reference image.

        Raises:
            NotFoundError: The path leaves the reference images directory, or the file is missing.
        """
        image_file = (self._data_directory / image_path).resolve()
        if not image_file.is_relative_to(self._images_directory.resolve()):
            raise NotFoundError(f"reference image {image_path} is outside its directory")
        if not image_file.is_file():
            raise NotFoundError(f"reference image {image_path} does not exist")
        return image_file

    def delete_image(self, image_path: str) -> None:
        """Deletes one image file, if it exists."""
        (self._data_directory / image_path).unlink(missing_ok=True)

    def delete_target_images(self, owner_id: str, target_id: str) -> None:
        """Deletes every image file of the target."""
        _require_path_component(owner_id)
        _require_path_component(target_id)
        shutil.rmtree(self._images_directory / owner_id / target_id, ignore_errors=True)

    def delete_owner_images(self, owner_id: str) -> None:
        """Deletes every image file of the owner."""
        _require_path_component(owner_id)
        shutil.rmtree(self._images_directory / owner_id, ignore_errors=True)


def _require_path_component(identifier: str) -> None:
    if identifier in FORBIDDEN_PATH_COMPONENTS or "/" in identifier or "\\" in identifier:
        raise ValueError(f"{identifier!r} cannot be used in a reference image path")
