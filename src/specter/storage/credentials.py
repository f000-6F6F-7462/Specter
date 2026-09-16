"""Encryption of camera stream passwords, and the secret files that keys and tokens live in."""

import os
from collections.abc import Callable
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from specter.core.errors import ConfigurationError

SECRET_FILE_PERMISSIONS = 0o600
SECRET_DIRECTORY_PERMISSIONS = 0o700


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
    """Returns the camera password encryption key, creating its file on first use.

    Raises:
        ConfigurationError: The key file cannot be created or read.
    """
    return load_or_create_secret(key_file, CredentialCipher.generate_key)


def load_or_create_secret(secret_file: Path, generate_secret: Callable[[], str]) -> str:
    """Returns the secret stored in the file, creating the file with a new secret on first use.

    The file and its directory are readable only by their owner.

    Raises:
        ConfigurationError: The secret file cannot be created or read.
    """
    if not secret_file.exists():
        _create_secret_file(secret_file, generate_secret)
    try:
        return secret_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ConfigurationError(f"cannot read the secret file {secret_file}: {error}") from error


def _create_secret_file(secret_file: Path, generate_secret: Callable[[], str]) -> None:
    # Several processes can start at once. Each writes its secret to a private temporary file and
    # then links it into place; linking fails when another process got there first, so every
    # process reads the same complete secret and never a half-written one.
    partial_file = secret_file.with_name(f".{secret_file.name}.{os.getpid()}.partial")
    try:
        secret_file.parent.mkdir(mode=SECRET_DIRECTORY_PERMISSIONS, parents=True, exist_ok=True)
        file_descriptor = os.open(
            partial_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, SECRET_FILE_PERMISSIONS
        )
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as partial_stream:
            partial_stream.write(generate_secret())
            # Without forcing the data and the link to disk, a power loss can leave an empty file,
            # and every camera password encrypted with a lost key becomes unrecoverable.
            partial_stream.flush()
            os.fsync(partial_stream.fileno())
        os.link(partial_file, secret_file)
        _synchronize_directory(secret_file.parent)
    except FileExistsError:
        pass
    except OSError as error:
        raise ConfigurationError(
            f"cannot create the secret file {secret_file}: {error}; "
            "point the security settings at a writable location"
        ) from error
    finally:
        partial_file.unlink(missing_ok=True)


def _synchronize_directory(directory: Path) -> None:
    directory_descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
