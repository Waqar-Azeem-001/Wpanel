"""One at a time for a job that talks to somebody else (a registrar, a hosting server) and costs money or makes something that cannot be made twice."""
import time
import uuid
from contextlib import contextmanager

from django.core.cache import cache
from rest_framework import status

from apps.core.exceptions import ServiceError

WAIT_SECONDS = 90
HOLD_SECONDS = 180  # a crashed worker frees the lock by itself after this


@contextmanager
def single_flight(name, *, wait=WAIT_SECONDS):
    """
    Only one caller at a time runs the block for ``name``; a twin that arrives meanwhile waits, then runs the block after the first is done
    (and so finds the work finished). Gives up with a clear error after ``wait`` seconds. Works across workers (the shared cache).
    """
    key, token = f"single-flight:{name}", uuid.uuid4().hex
    deadline = time.monotonic() + wait
    while not cache.add(key, token, HOLD_SECONDS):
        if time.monotonic() > deadline:
            raise ServiceError("This is already being done. Try again in a minute.", code="in_progress", status_code=status.HTTP_409_CONFLICT)
        time.sleep(0.1)
    try:
        yield
    finally:
        if cache.get(key) == token:
            cache.delete(key)
