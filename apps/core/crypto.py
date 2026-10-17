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


def encrypt_bytes(data: bytes) -> bytes:
    """Encrypt a whole file's worth of bytes with the same key as stored credentials (used for the panel's own database backup)."""
    return _fernet().encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    try:
        return _fernet().decrypt(token)
    except InvalidToken as exc:
        raise ValueError("This backup cannot be decrypted with the current key.") from exc


# --- big files, in pieces -------------------------------------------------------------------------------------

FILE_MAGIC = b"WHPB1\n"
FILE_CHUNK = 32 * 1024 * 1024


def encrypt_file(source, target):
    """Encrypt a (possibly large) file piece by piece, so it never has to fit in memory: the magic, then for each piece 4 bytes of length and the encrypted piece."""
    fernet = _fernet()
    with open(source, "rb") as src, open(target, "wb") as dst:
        dst.write(FILE_MAGIC)
        while chunk := src.read(FILE_CHUNK):
            token = fernet.encrypt(chunk)
            dst.write(len(token).to_bytes(4, "big"))
            dst.write(token)


def decrypt_file(source, target):
    """The reverse of ``encrypt_file``. A file written before that (one encrypted block) is read too. Raises ValueError for a wrong key or a damaged file."""
    fernet = _fernet()
    with open(source, "rb") as src, open(target, "wb") as dst:
        head = src.read(len(FILE_MAGIC))
        try:
            if head != FILE_MAGIC:  # an older backup: the whole file is one encrypted block
                dst.write(fernet.decrypt(head + src.read()))
                return
            while True:
                size = src.read(4)
                if not size:
                    return
                token = src.read(int.from_bytes(size, "big"))
                dst.write(fernet.decrypt(token))
        except InvalidToken as exc:
            raise ValueError("This backup cannot be decrypted with the current key.") from exc
