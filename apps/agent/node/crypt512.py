"""
SHA-512 crypt (``$6$``), the password format Dovecot reads as ``{SHA512-CRYPT}``. Python 3.13 no longer ships ``crypt``, so it is written here from Drepper's published
specification and checked against the specification's own test vector (see the tests). The password never has to be written down: only this hash reaches the node's files.
"""
import hashlib
import secrets

ALPHABET = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
ORDER = [(0, 21, 42), (22, 43, 1), (44, 2, 23), (3, 24, 45), (25, 46, 4), (47, 5, 26), (6, 27, 48), (28, 49, 7), (50, 8, 29), (9, 30, 51), (31, 52, 10),
         (53, 11, 32), (12, 33, 54), (34, 55, 13), (56, 14, 35), (15, 36, 57), (37, 58, 16), (59, 17, 38), (18, 39, 60), (40, 61, 19), (62, 20, 41)]


def _stretch(block, length):
    return (block * (length // 64 + 1))[:length]


def _b64(value, count):
    out = ""
    for _ in range(count):
        out += ALPHABET[value & 0x3F]
        value >>= 6
    return out


def sha512_crypt(password, salt, rounds=5000):
    pw, salt = password.encode(), salt.encode()[:16]
    b = hashlib.sha512(pw + salt + pw).digest()
    a = hashlib.sha512(pw + salt + _stretch(b, len(pw)))
    n = len(pw)
    while n > 0:
        a.update(b if n & 1 else pw)
        n >>= 1
    da = a.digest()
    p = _stretch(hashlib.sha512(pw * len(pw)).digest(), len(pw))
    s = _stretch(hashlib.sha512(salt * (16 + da[0])).digest(), len(salt))
    prev = da
    for i in range(rounds):
        c = hashlib.sha512()
        c.update(p if i & 1 else prev)
        if i % 3:
            c.update(s)
        if i % 7:
            c.update(p)
        c.update(prev if i & 1 else p)
        prev = c.digest()
    text = "".join(_b64((prev[x] << 16) | (prev[y] << 8) | prev[z], 4) for x, y, z in ORDER) + _b64(prev[63], 2)
    prefix = "$6$" + (f"rounds={rounds}$" if rounds != 5000 else "")
    return f"{prefix}{salt.decode()}${text}"


def hash_password(password):
    """A fresh ``{SHA512-CRYPT}`` hash with a random salt, ready for Dovecot's password file."""
    return "{SHA512-CRYPT}" + sha512_crypt(password, "".join(secrets.choice(ALPHABET) for _ in range(16)))
