"""Fernet-based at-rest encryption for small JSON blobs (stream credentials).

``Fernet`` needs a 32-byte urlsafe-base64 key; operators configure an arbitrary
passphrase (``SPECTER_SECURITY__SECRET_KEY``), so it is stretched into a valid key
via SHA-256 rather than requiring a pre-formatted Fernet key.
"""

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from specter.core.errors import DependencyFailure


def _key(secret: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def encrypt_json(data: dict[str, Any], secret: str) -> str:
    return Fernet(_key(secret)).encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str, secret: str) -> dict[str, Any]:
    try:
        raw = Fernet(_key(secret)).decrypt(token.encode())
    except InvalidToken as exc:
        raise DependencyFailure("stream credentials could not be decrypted") from exc
    return dict(json.loads(raw))
