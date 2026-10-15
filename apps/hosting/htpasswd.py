"""
The password lines Apache reads from a ``.htpasswd`` file, in its ``$apr1$`` (salted MD5, 1000 rounds) form. Apache on every platform understands this form, and the
password is never stored anywhere in the clear: only this line is written to the server.
"""
import hashlib
import secrets

_ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _to64(value, count):
    out = ""
    for _ in range(count):
        out += _ITOA64[value & 0x3F]
        value >>= 6
    return out


def apr1(password, salt=None):
    """``$apr1$salt$hash`` for ``password`` (a str). ``salt`` (8 characters of ./0-9A-Za-z) is made when not given."""
    pw = password.encode("utf-8")
    salt = (salt or "".join(secrets.choice(_ITOA64) for _ in range(8)))[:8]
    s = salt.encode("ascii")
    magic = b"$apr1$"
    final = hashlib.md5(pw + s + pw).digest()
    ctx = pw + magic + s
    length = len(pw)
    while length > 0:
        ctx += final[: min(16, length)]
        length -= 16
    i = len(pw)
    while i:
        ctx += b"\x00" if i & 1 else pw[:1]
        i >>= 1
    final = hashlib.md5(ctx).digest()
    for i in range(1000):
        block = pw if i & 1 else final
        if i % 3:
            block += s
        if i % 7:
            block += pw
        block += final if i & 1 else pw
        final = hashlib.md5(block).digest()
    out = ""
    for a, b, c in ((0, 6, 12), (1, 7, 13), (2, 8, 14), (3, 9, 15), (4, 10, 5)):
        out += _to64((final[a] << 16) | (final[b] << 8) | final[c], 4)
    out += _to64(final[11], 2)
    return f"$apr1${salt}${out}"


def line(login, password):
    return f"{login}:{apr1(password)}"
