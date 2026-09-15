"""Evidence snapshots on the device's disk, and how long they are kept."""

import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath

from specter.core.errors import NotFoundError

EVIDENCE_DIRECTORY_NAME = "evidence"
SNAPSHOT_FILE_SUFFIX = ".jpg"
PARTIAL_FILE_SUFFIX = ".partial"
FORBIDDEN_PATH_COMPONENTS = frozenset({"", ".", ".."})


@dataclass(frozen=True, slots=True)
class RetentionReport:
    """What one retention pass removed, and how much evidence is left."""

    # Relative to the data directory, in the same form as alerts' snapshot paths.
    removed_day_directories: tuple[str, ...]
    remaining_size_bytes: int


@dataclass(frozen=True, slots=True)
class _DayDirectory:
    day: date
    path: Path
    size_bytes: int


class EvidenceStore:
    """Writes evidence snapshots under the data directory and removes old ones.

    Snapshots are grouped in one directory per owner and day, so retention removes whole days
    at a time instead of deciding file by file.
    """

    def __init__(self, data_directory: Path) -> None:
        self._data_directory = data_directory
        self._evidence_directory = data_directory / EVIDENCE_DIRECTORY_NAME

    def build_snapshot_path(self, owner_id: str, alert_id: str, captured_at: datetime) -> str:
        """Returns where an alert's snapshot belongs, relative to the data directory.

        Raises:
            ValueError: An id could escape the evidence directory.
        """
        for identifier in (owner_id, alert_id):
            if identifier in FORBIDDEN_PATH_COMPONENTS or "/" in identifier or "\\" in identifier:
                raise ValueError(f"{identifier!r} cannot be used in an evidence path")
        captured_day = captured_at.astimezone(UTC)
        return str(
            PurePosixPath(
                EVIDENCE_DIRECTORY_NAME,
                owner_id,
                f"{captured_day:%Y}",
                f"{captured_day:%m}",
                f"{captured_day:%d}",
                f"{alert_id}{SNAPSHOT_FILE_SUFFIX}",
            )
        )

    def write_snapshot(self, snapshot_path: str, jpeg_bytes: bytes) -> None:
        """Writes the snapshot through a temporary file, so a crash never leaves half an image."""
        destination_file = self.resolve_snapshot_file(snapshot_path, must_exist=False)
        destination_file.parent.mkdir(parents=True, exist_ok=True)
        partial_file = destination_file.with_name(destination_file.name + PARTIAL_FILE_SUFFIX)
        partial_file.write_bytes(jpeg_bytes)
        partial_file.replace(destination_file)

    def resolve_snapshot_file(self, snapshot_path: str, *, must_exist: bool = True) -> Path:
        """Returns the absolute file of a snapshot path.

        Raises:
            NotFoundError: The path leaves the evidence directory, or the file does not exist
                while ``must_exist`` is set.
        """
        snapshot_file = (self._data_directory / snapshot_path).resolve()
        if not snapshot_file.is_relative_to(self._evidence_directory.resolve()):
            raise NotFoundError(f"snapshot {snapshot_path} is outside the evidence directory")
        if must_exist and not snapshot_file.is_file():
            raise NotFoundError(f"snapshot {snapshot_path} does not exist")
        return snapshot_file

    def enforce_retention(
        self, today: date, maximum_age_days: int, maximum_size_bytes: int
    ) -> RetentionReport:
        """Removes days older than the maximum age, then the oldest days until the quota fits.

        The current day is removed too if it alone exceeds the quota, because a full disk would
        stop every camera from recording evidence.
        """
        oldest_kept_day = today - timedelta(days=maximum_age_days)
        kept_day_directories: list[_DayDirectory] = []
        removed_day_directories: list[str] = []

        for day_directory in self._list_day_directories_oldest_first():
            if day_directory.day < oldest_kept_day:
                removed_day_directories.append(self._remove_day_directory(day_directory))
            else:
                kept_day_directories.append(day_directory)

        remaining_size_bytes = sum(directory.size_bytes for directory in kept_day_directories)
        while remaining_size_bytes > maximum_size_bytes and kept_day_directories:
            oldest_day_directory = kept_day_directories.pop(0)
            removed_day_directories.append(self._remove_day_directory(oldest_day_directory))
            remaining_size_bytes -= oldest_day_directory.size_bytes

        return RetentionReport(
            removed_day_directories=tuple(removed_day_directories),
            remaining_size_bytes=remaining_size_bytes,
        )

    def _list_day_directories_oldest_first(self) -> list[_DayDirectory]:
        if not self._evidence_directory.is_dir():
            return []
        day_directories: list[_DayDirectory] = []
        for path in self._evidence_directory.glob("*/*/*/*"):
            day = _parse_day(path)
            if path.is_dir() and day is not None:
                day_directories.append(
                    _DayDirectory(day=day, path=path, size_bytes=_measure_size_bytes(path))
                )
        return sorted(day_directories, key=lambda directory: directory.day)

    def _remove_day_directory(self, day_directory: _DayDirectory) -> str:
        shutil.rmtree(day_directory.path)
        return day_directory.path.relative_to(self._data_directory).as_posix()


def _parse_day(day_directory: Path) -> date | None:
    try:
        return date(
            int(day_directory.parent.parent.name),
            int(day_directory.parent.name),
            int(day_directory.name),
        )
    except ValueError:
        return None


def _measure_size_bytes(directory: Path) -> int:
    return sum(file.stat().st_size for file in directory.rglob("*") if file.is_file())
