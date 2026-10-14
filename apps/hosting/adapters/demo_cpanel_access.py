"""
The demo provider's stand-in for "Open cPanel": a one-time sign-in link to a sample page, so the whole flow can be seen and tried without a
cPanel server. The link carries a random token that works once and for two minutes; only a hash of it is kept, beside the account's other
sample state. A real provider (the cPanel adapter) returns its own one-time link through the same ``CpanelAccessCapability`` contract.
"""
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

LIFETIME_SECONDS = 120


def _file(state_dir):
    return Path(state_dir) / "cpanel_session.json"


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(state_dir):
    """A new token and when it stops working; any older unused token stops working at once."""
    token = secrets.token_urlsafe(24)
    expires = int(time.time()) + LIFETIME_SECONDS
    _file(state_dir).parent.mkdir(parents=True, exist_ok=True)
    _file(state_dir).write_text(json.dumps({"hash": _digest(token), "expires": expires}), encoding="utf-8")
    return token, expires


def consume_token(state_dir, token):
    """True once for a valid, unexpired token; the token is spent by being checked."""
    try:
        data = json.loads(_file(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    _file(state_dir).unlink(missing_ok=True)  # spent whether or not it matched: a wrong guess also burns the real link
    return bool(token) and time.time() <= data.get("expires", 0) and hmac.compare_digest(data.get("hash", ""), _digest(token))
