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
