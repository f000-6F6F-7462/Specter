"""Encryption of camera stream passwords, and the key file that holds the encryption key."""

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from specter.core.errors import ConfigurationError

KEY_FILE_PERMISSIONS = 0o600
KEY_DIRECTORY_PERMISSIONS = 0o700


class CredentialCipher:
    """Encrypts and decrypts camera passwords with a Fernet key."""

    def __init__(self, fernet_key: str) -> None:
        try:
            self._fernet = Fernet(fernet_key)
        except ValueError as error:
            raise ConfigurationError("the credentials key is not a valid Fernet key") from error

    @staticmethod
    def generate_key() -> str:
        """Returns a new random Fernet key."""
        return Fernet.generate_key().decode()

    def encrypt(self, password: str) -> str:
        """Returns the password encrypted as text that is safe to store."""
        return self._fernet.encrypt(password.encode()).decode()

    def decrypt(self, encrypted_password: str) -> str:
        """Returns the original password.

        Raises:
            ConfigurationError: The password was encrypted with a different key.
        """
        try:
            return self._fernet.decrypt(encrypted_password.encode()).decode()
        except InvalidToken as error:
            raise ConfigurationError(
                "a camera password was encrypted with a different credentials key"
            ) from error


def load_or_create_credentials_key(key_file: Path) -> str:
    """Returns the key stored in the file, creating the file with a new key on first use.

    The file and its directory are readable only by their owner.

    Raises:
        ConfigurationError: The key file cannot be created or read.
    """
    if not key_file.exists():
        _create_key_file(key_file)
    try:
        return key_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ConfigurationError(
            f"cannot read the credentials key file {key_file}: {error}"
        ) from error


def _create_key_file(key_file: Path) -> None:
    # Several processes can start at once. Each writes its key to a private temporary file and then
    # links it into place; linking fails when another process got there first, so every process
    # reads the same complete key and never a half-written one.
    partial_file = key_file.with_name(f".{key_file.name}.{os.getpid()}.partial")
    try:
        key_file.parent.mkdir(mode=KEY_DIRECTORY_PERMISSIONS, parents=True, exist_ok=True)
        file_descriptor = os.open(
            partial_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, KEY_FILE_PERMISSIONS
        )
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as partial_stream:
            partial_stream.write(CredentialCipher.generate_key())
            # Without forcing the data and the link to disk, a power loss can leave an empty key
            # file, and every camera password encrypted with the lost key becomes unrecoverable.
            partial_stream.flush()
            os.fsync(partial_stream.fileno())
        os.link(partial_file, key_file)
        _synchronize_directory(key_file.parent)
    except FileExistsError:
        pass
    except OSError as error:
        raise ConfigurationError(
            f"cannot create the credentials key file {key_file}: {error}; "
            "set SPECTER_SECURITY__CREDENTIALS_KEY_FILE to a writable location"
        ) from error
    finally:
        partial_file.unlink(missing_ok=True)


def _synchronize_directory(directory: Path) -> None:
    directory_descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
