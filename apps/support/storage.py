"""
Private storage for ticket attachments.

Attachments belong to one client and may contain anything a customer chose to upload, so they must never sit
under ``MEDIA_ROOT`` (which nginx serves to the world). They live under ``PRIVATE_MEDIA_ROOT`` instead and are
reachable only through an authenticated view that checks who is asking (``views_customer.attachment``).
"""
import os
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils import timezone
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateStorage(FileSystemStorage):
    """A file store rooted at ``settings.PRIVATE_MEDIA_ROOT``, read on every use (so tests can redirect it)."""

    @property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @property
    def location(self):
        return os.path.abspath(settings.PRIVATE_MEDIA_ROOT)

    @property
    def base_url(self):
        return None  # never addressable by URL: downloads go through a permission-checked view


def attachment_path(instance, filename):
    """A random, unguessable path: the uploader's filename is only kept as display text, never as a path."""
    extension = ("." + filename.rsplit(".", 1)[1].lower()) if "." in filename else ""
    return f"support/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{extension}"
