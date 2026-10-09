"""
The rules for FTP logins, shared by every provider that offers them (the demo provider and the cPanel adapter): a login name of 2 to 32 plain
characters, and a strong password (10 to 128 characters, upper and lower case letters and numbers, not one character repeated).
"""
import re

from .base import HostingError

FTP_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$")


def check_ftp_password(password):
    password = password or ""
    if len(password) < 10 or len(password) > 128:
        raise HostingError("Use a password of 10 to 128 characters.")
    if len({c for c in password}) < 5 or not (re.search(r"[a-z]", password) and re.search(r"[A-Z]", password) and re.search(r"\d", password)):
        raise HostingError("Mix upper and lower case letters and numbers, and avoid repeating characters.")
    return password
