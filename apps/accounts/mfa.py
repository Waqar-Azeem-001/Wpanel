"""
TOTP two-factor sign-in (RFC 6238/4226): the maths only, no database and no request — ``services`` and ``views`` call
this. Deliberately dependency-free (no third-party authenticator library): the algorithm is a page of well-known HMAC
arithmetic, and not adding a package for it keeps one less thing to trust and to keep patched.

A person adds the *secret* to an authenticator app (Google Authenticator, Authy, 1Password...), either by scanning a
QR code built from ``provisioning_uri`` or by typing the ``formatted`` secret in by hand. The app and this module then
compute the same 6-digit code every 30 seconds from the secret and the time, with nothing sent over the network.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time

SECRET_BYTES = 20  # 160 bits, the size RFC 4226 recommends
DIGITS = 6
PERIOD = 30  # seconds per code
WINDOW = 1  # accepted codes: this step, one before, one after (clock drift)
RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"  # no 0/1/l/o/i: nothing that is read back wrong


def generate_secret() -> str:
    """A fresh random secret, base32-encoded (the form every authenticator app expects)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def formatted(secret: str) -> str:
    """The secret in groups of 4 characters, easier to type by hand into an app: 'ABCD EFGH ...'."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


def _decode(secret: str) -> bytes:
    padded = secret.strip().replace(" ", "").upper()
    padded += "=" * (-len(padded) % 8)
    return base64.b32decode(padded)


def totp_at(secret: str, at: float, *, period: int = PERIOD, digits: int = DIGITS) -> str:
    """The code for one point in time (RFC 6238); ``at`` is a Unix timestamp."""
    counter = int(at // period)
    key = _decode(secret)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    part = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(part % (10 ** digits)).zfill(digits)


def verify(secret: str, code: str, *, at: float = None, window: int = WINDOW) -> bool:
    """Whether ``code`` is correct for now (or the ``window`` steps either side of it, for clock drift)."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS or not secret:
        return False
    now = time.time() if at is None else at
    return any(hmac.compare_digest(code, totp_at(secret, now + step * PERIOD)) for step in range(-window, window + 1))


def provisioning_uri(secret: str, *, email: str, issuer: str) -> str:
    """The ``otpauth://`` address an authenticator app reads (typed in, pasted, or turned into a QR code)."""
    from urllib.parse import quote

    label = quote(f"{issuer}:{email}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
           f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}")


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list:
    """``count`` fresh single-use codes, e.g. 'a7k9-h3mn-8pqr', for when the authenticator app is not to hand."""
    def one():
        raw = "".join(secrets.choice(RECOVERY_CODE_ALPHABET) for _ in range(12))
        return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"

    return [one() for _ in range(count)]


def normalize_recovery_code(code: str) -> str:
    """A recovery code as typed, case- and dash-insensitive, for comparing against what was issued."""
    return "".join((code or "").lower().split()).replace("-", "")
