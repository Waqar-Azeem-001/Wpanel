"""Symmetric encryption for provider credentials stored in the database."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    key = settings.CREDENTIALS_ENCRYPTION_KEY
    if not key:
        digest = hashlib.sha256(("wpanel-credentials:" + settings.SECRET_KEY).encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored credential cannot be decrypted with the current key.") from exc
