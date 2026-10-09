"""
Signed command envelopes (N1).

WHPanel signs the exact bytes of the payload with an Ed25519 key; the agent holds only the public key, so a node that is taken over can verify
commands but can never forge one. The envelope is ``{"payload": base64(bytes), "signature": base64(sig)}`` and the agent verifies the signature
over the received bytes *before* parsing them, so there is no canonical-JSON problem between Python and Go.

The payload carries: protocol version, job id, correlation id, target node, command, parameters, issue and expiry time, a random nonce and an
idempotency key. The agent refuses a payload that is not for it, is expired, repeats a nonce it has seen, or names a command or parameter it does
not allow. ``ReplayGuard`` is the agent-side nonce memory used by the reference agent.
"""
import base64
import hashlib
import json
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings

from . import catalog

CLOCK_SKEW = 60  # seconds either side that the agent tolerates between its clock and WHPanel's


class EnvelopeError(ValueError):
    pass


def _private_key():
    configured = getattr(settings, "AGENT_SIGNING_KEY", "")
    if configured:
        seed = base64.b64decode(configured)
    else:  # development and tests only: production sets AGENT_SIGNING_KEY (see docs/n1-wh-agent.md)
        seed = hashlib.sha256(("wpanel-agent-signing:" + settings.SECRET_KEY).encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_key_b64():
    raw = _private_key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def generate_key():
    """A new signing key as base64, for ``AGENT_SIGNING_KEY``."""
    raw = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    return base64.b64encode(raw).decode()


def sign(payload: dict) -> dict:
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return {"payload": base64.b64encode(data).decode(), "signature": base64.b64encode(_private_key().sign(data)).decode()}


def verify(envelope, *, public_key, node, seen=None, now=None):
    """
    What an agent does with an envelope. Returns the cleaned payload, or raises EnvelopeError.
    ``public_key`` is the base64 public key, ``node`` the agent's own id, ``seen`` a ReplayGuard.
    """
    now = time.time() if now is None else now
    try:
        data = base64.b64decode(envelope["payload"], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key)).verify(signature, data)
    except (KeyError, TypeError, ValueError, InvalidSignature):
        raise EnvelopeError("The signature is not valid.") from None
    try:
        payload = json.loads(data)
    except ValueError:
        raise EnvelopeError("The payload is not JSON.") from None
    if not isinstance(payload, dict) or payload.get("v") != catalog.PROTOCOL_VERSION:
        raise EnvelopeError("Unsupported protocol version.")
    if payload.get("node") != node:
        raise EnvelopeError("This command is for another node.")
    issued, expires = payload.get("issued_at"), payload.get("expires_at")
    if not isinstance(issued, int) or not isinstance(expires, int) or issued > now + CLOCK_SKEW:
        raise EnvelopeError("The command is dated in the future.")
    if expires < now:
        raise EnvelopeError("The command has expired.")
    nonce = payload.get("nonce")
    if not isinstance(nonce, str) or len(nonce) < 16:
        raise EnvelopeError("The nonce is missing.")
    try:
        params = catalog.validate(payload.get("command"), payload.get("params"))
    except catalog.InvalidCommand as exc:
        raise EnvelopeError(str(exc)) from None
    if seen is not None and not seen.remember(nonce, expires):
        raise EnvelopeError("This command was already received (replay).")
    return {**payload, "params": params}


class ReplayGuard:
    """Remembers nonces until they could no longer be valid. The Go agent keeps the same table on disk so a restart does not reopen the window."""

    def __init__(self):
        self._seen = {}

    def remember(self, nonce, expires):
        now = time.time()
        self._seen = {n: e for n, e in self._seen.items() if e + CLOCK_SKEW >= now}
        if nonce in self._seen:
            return False
        self._seen[nonce] = expires
        return True
