"""
How a connected storefront (e.g. a WordPress plugin) authenticates to the storefront-facing API - a dedicated,
read-only credential, never a staff/customer session or JWT (Rule: no staff privileges inherited by an integration
credential). Presented the same way DRF's own TokenAuthentication expects a token: an ``Authorization`` header,
just with its own scheme keyword so it can never be confused with a user's own bearer token.

    Authorization: Storefront <public_id>:<secret>
"""
from django.contrib.auth.models import AnonymousUser
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from . import services

SCHEME = "Storefront"


class StorefrontKeyAuthentication(BaseAuthentication):
    """
    On success, ``request.user`` is ``AnonymousUser`` (a storefront is not a Django user and must never inherit a
    staff/customer identity or its permissions) and ``request.auth`` is the ``ExternalStorefront`` - views and the
    permission class read the storefront from there, and only there.
    """

    keyword = SCHEME

    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header:
            return None
        try:
            scheme, credential = header.split(" ", 1)
        except ValueError:
            return None
        if scheme != self.keyword:
            return None  # let another configured authenticator (JWT, session) try instead
        try:
            public_id, raw_secret = credential.strip().split(":", 1)
        except ValueError:
            raise AuthenticationFailed("Malformed credential.")
        storefront = services.authenticate_storefront(public_id, raw_secret)
        if storefront is None:
            raise AuthenticationFailed("Invalid or disabled storefront credential.")
        return AnonymousUser(), storefront

    def authenticate_header(self, request):
        return self.keyword


class StorefrontAuthScheme(OpenApiAuthenticationExtension):
    """So the generated API docs (``/api/v1/docs/``) describe this scheme instead of silently ignoring it."""

    target_class = StorefrontKeyAuthentication
    name = "StorefrontAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey", "in": "header", "name": "Authorization",
            "description": 'A storefront\'s own credential, never a staff/customer session: '
                          '"Authorization: Storefront <public_id>:<secret>".',
        }
