import os
import stat
from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest

from specter.core.errors import ConfigurationError
from specter.storage.credentials import CredentialCipher, load_or_create_credentials_key


def test_password_is_recovered_when_encrypted_and_decrypted() -> None:
    cipher = CredentialCipher(CredentialCipher.generate_key())

    encrypted_password = cipher.encrypt("camera-password")

    assert encrypted_password != "camera-password"
    assert cipher.decrypt(encrypted_password) == "camera-password"


def test_decryption_fails_when_a_different_key_is_used() -> None:
    encrypted_password = CredentialCipher(CredentialCipher.generate_key()).encrypt("secret")

    with pytest.raises(ConfigurationError, match="different credentials key"):
        CredentialCipher(CredentialCipher.generate_key()).decrypt(encrypted_password)


def test_cipher_is_rejected_when_key_is_not_a_fernet_key() -> None:
    with pytest.raises(ConfigurationError, match="not a valid Fernet key"):
        CredentialCipher("not-a-fernet-key")


def test_key_file_is_created_with_a_valid_key_when_missing(tmp_path: Path) -> None:
    key_file = tmp_path / "secrets" / "credentials.key"

    key = load_or_create_credentials_key(key_file)

    assert CredentialCipher(key).decrypt(CredentialCipher(key).encrypt("secret")) == "secret"
    assert key_file.read_text(encoding="utf-8").strip() == key


def test_key_file_is_readable_only_by_its_owner_when_created(tmp_path: Path) -> None:
    key_file = tmp_path / "secrets" / "credentials.key"

    load_or_create_credentials_key(key_file)

    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_file.parent.stat().st_mode) == 0o700


def test_same_key_is_returned_when_key_file_already_exists(tmp_path: Path) -> None:
    key_file = tmp_path / "credentials.key"

    first_key = load_or_create_credentials_key(key_file)
    second_key = load_or_create_credentials_key(key_file)

    assert first_key == second_key


def test_no_temporary_file_is_left_behind_when_key_file_is_created(tmp_path: Path) -> None:
    load_or_create_credentials_key(tmp_path / "credentials.key")

    assert [path.name for path in tmp_path.iterdir()] == ["credentials.key"]


def test_key_and_its_directory_entry_are_forced_to_disk_when_key_file_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synchronized_is_directory: list[bool] = []
    monkeypatch.setattr(os, "fsync", partial(_record_fsync, synchronized_is_directory, os.fsync))

    load_or_create_credentials_key(tmp_path / "credentials.key")

    assert synchronized_is_directory == [False, True]


def test_loading_fails_when_key_file_cannot_be_created(tmp_path: Path) -> None:
    read_only_directory = tmp_path / "read_only"
    read_only_directory.mkdir(mode=0o500)

    with pytest.raises(ConfigurationError, match="cannot create the credentials key file"):
        load_or_create_credentials_key(read_only_directory / "credentials.key")


def _record_fsync(
    synchronized_is_directory: list[bool],
    original_fsync: Callable[[int], None],
    file_descriptor: int,
) -> None:
    synchronized_is_directory.append(stat.S_ISDIR(os.fstat(file_descriptor).st_mode))
    original_fsync(file_descriptor)
