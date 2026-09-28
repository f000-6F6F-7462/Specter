"""Reading and writing cameras and the watchlists they use."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime

from peewee import IntegrityError

from specter.core.errors import ConfigurationError, InvalidEntityError, NotFoundError
from specter.entities.cameras import (
    Camera,
    CameraCredentials,
    DesiredState,
    SamplingMode,
    SamplingSettings,
)
from specter.storage.columns import dump_json, format_utc_timestamp, load_json
from specter.storage.credentials import CredentialCipher
from specter.storage.database import database_proxy
from specter.storage.queries import delete_rows
from specter.storage.tables import CameraRecord, CameraWatchlistRecord


def save_camera(camera: Camera, cipher: CredentialCipher | None) -> None:
    """Inserts the camera, or replaces its stored fields and watchlists if it already exists.

    Args:
        cipher: Needed only when the camera has credentials.

    Raises:
        ConfigurationError: The camera has credentials but no cipher was given.
        InvalidEntityError: The camera uses a watchlist that does not exist.
    """
    record_values = _build_record_values(camera, cipher)
    try:
        with database_proxy.atomic():
            updated_row_count = (
                CameraRecord.update(**record_values).where(CameraRecord.id == camera.id).execute()
            )
            if updated_row_count == 0:
                CameraRecord.insert(
                    id=camera.id,
                    created_at=format_utc_timestamp(datetime.now(UTC)),
                    **record_values,
                ).execute()
            delete_rows(CameraWatchlistRecord, CameraWatchlistRecord.camera_id == camera.id)
            if camera.watchlist_ids:
                CameraWatchlistRecord.insert_many(
                    [
                        {"camera_id": camera.id, "watchlist_id": watchlist_id, "position": position}
                        for position, watchlist_id in enumerate(camera.watchlist_ids)
                    ]
                ).execute()
    except IntegrityError as error:
        raise InvalidEntityError(
            f"camera {camera.id} uses a watchlist that does not exist"
        ) from error


def find_camera(camera_id: str, cipher: CredentialCipher | None) -> Camera | None:
    """Returns the camera, or None if it does not exist."""
    record = CameraRecord.get_or_none(CameraRecord.id == camera_id)
    if record is None:
        return None
    return _build_cameras([record], cipher)[0]


def find_camera_settings(camera_id: str) -> Camera | None:
    """Returns the camera without its credentials, or None if it does not exist.

    Serves processes that never hold the credentials key. The result must never be saved, because
    saving it would delete the camera's stored password.
    """
    record = CameraRecord.get_or_none(CameraRecord.id == camera_id)
    if record is None:
        return None
    return _build_cameras([record], cipher=None, include_credentials=False)[0]


def list_owner_cameras(owner_id: str, cipher: CredentialCipher | None) -> list[Camera]:
    """Returns the owner's cameras, oldest first."""
    records = (
        CameraRecord.select()
        .where(CameraRecord.owner_id == owner_id)
        .order_by(CameraRecord.created_at)
    )
    return _build_cameras(list(records), cipher)


def list_cameras_to_run(cipher: CredentialCipher | None) -> list[Camera]:
    """Returns every enabled camera whose owner wants it running, oldest first."""
    records = (
        CameraRecord.select()
        .where(CameraRecord.is_enabled, CameraRecord.desired_state == DesiredState.RUNNING.value)
        .order_by(CameraRecord.created_at)
    )
    return _build_cameras(list(records), cipher)


def delete_camera(camera_id: str) -> None:
    """Deletes the camera together with its zones and rules.

    Raises:
        NotFoundError: The camera does not exist.
    """
    deleted_row_count = delete_rows(CameraRecord, CameraRecord.id == camera_id)
    if deleted_row_count == 0:
        raise NotFoundError(f"camera {camera_id} does not exist")


def _build_record_values(camera: Camera, cipher: CredentialCipher | None) -> dict[str, object]:
    return {
        "owner_id": camera.owner_id,
        "name": camera.name,
        "source_url": camera.source_url,
        "credentials_username": camera.credentials.username if camera.credentials else None,
        "credentials_encrypted_password": _encrypt_password(camera.credentials, cipher),
        "detection_classes_json": dump_json(sorted(camera.detection_classes)),
        "sampling_mode": camera.sampling.mode.value,
        "target_fps": camera.sampling.target_fps,
        "minimum_fps": camera.sampling.minimum_fps,
        "is_motion_gating_enabled": camera.sampling.is_motion_gating_enabled,
        "is_enabled": camera.is_enabled,
        "desired_state": camera.desired_state.value,
        "metadata_json": dump_json(dict(camera.metadata)),
    }


def _encrypt_password(
    credentials: CameraCredentials | None, cipher: CredentialCipher | None
) -> str | None:
    if credentials is None:
        return None
    if cipher is None:
        raise ConfigurationError("a credentials key is required to store camera passwords")
    return cipher.encrypt(credentials.password)


def _build_cameras(
    records: list[CameraRecord],
    cipher: CredentialCipher | None,
    *,
    include_credentials: bool = True,
) -> list[Camera]:
    watchlist_ids_by_camera_id = _load_watchlist_ids_by_camera_id(record.id for record in records)
    return [
        Camera(
            id=record.id,
            owner_id=record.owner_id,
            name=record.name,
            source_url=record.source_url,
            credentials=_build_credentials(record, cipher) if include_credentials else None,
            watchlist_ids=watchlist_ids_by_camera_id.get(record.id, ()),
            detection_classes=frozenset(load_json(record.detection_classes_json)),
            sampling=SamplingSettings(
                mode=SamplingMode(record.sampling_mode),
                target_fps=record.target_fps,
                minimum_fps=record.minimum_fps,
                is_motion_gating_enabled=record.is_motion_gating_enabled,
            ),
            is_enabled=record.is_enabled,
            desired_state=DesiredState(record.desired_state),
            metadata=load_json(record.metadata_json),
        )
        for record in records
    ]


def _build_credentials(
    record: CameraRecord, cipher: CredentialCipher | None
) -> CameraCredentials | None:
    if record.credentials_username is None or record.credentials_encrypted_password is None:
        return None
    if cipher is None:
        raise ConfigurationError("a credentials key is required to read camera passwords")
    return CameraCredentials(
        username=record.credentials_username,
        password=cipher.decrypt(record.credentials_encrypted_password),
    )


def _load_watchlist_ids_by_camera_id(camera_ids: Iterable[str]) -> dict[str, tuple[str, ...]]:
    watchlist_ids_by_camera_id: defaultdict[str, list[str]] = defaultdict(list)
    records = (
        CameraWatchlistRecord.select()
        .where(CameraWatchlistRecord.camera_id.in_(list(camera_ids)))
        .order_by(CameraWatchlistRecord.position)
    )
    for record in records:
        watchlist_ids_by_camera_id[record.camera_id].append(record.watchlist_id)
    return {
        camera_id: tuple(watchlist_ids)
        for camera_id, watchlist_ids in watchlist_ids_by_camera_id.items()
    }
