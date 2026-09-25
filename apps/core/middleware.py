import re
import uuid

from .request_context import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestIDMiddleware:
    """Assigns a correlation ID to every request and echoes it in the response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_ID.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
        response[REQUEST_ID_HEADER] = request_id
        return response


class StripNullBytesMiddleware:
    """
    Remove NUL (0x00) characters from query strings and submitted forms before any view sees them.

    PostgreSQL refuses text containing a NUL byte, so ``?q=%00`` or a form field holding one turned a search box into a
    server error (SQLite quietly accepted it, which hid the problem). Django's form fields already reject NUL; this
    covers the raw ``request.GET`` / ``request.POST`` values that views hand to the database directly. API bodies are
    left alone (the serializers reject NUL themselves).
    """

    FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _cleaned(querydict):
        if not any("\x00" in key or any("\x00" in v for v in values) for key, values in querydict.lists()):
            return querydict
        copy = querydict.__class__("", mutable=True, encoding=querydict.encoding)
        for key, values in querydict.lists():
            copy.setlist(key.replace("\x00", ""), [v.replace("\x00", "") for v in values])
        copy._mutable = False
        return copy

    def __call__(self, request):
        request.GET = self._cleaned(request.GET)
        if (request.method in ("POST", "PUT", "PATCH") and not request.path.startswith("/api/")
                and request.content_type in self.FORM_TYPES):
            request.POST = self._cleaned(request.POST)
        return self.get_response(request)
