"""
Consistent API error format.

Every error response has the shape::

    {"error": {"code": "<machine_code>", "message": "<human text>",
               "details": {...} | null, "request_id": "<id>"}}
"""
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from .request_context import get_request_id


class ServiceError(Exception):
    """Raised by the service layer for business-rule violations."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "service_error"

    def __init__(self, message, code=None, details=None, status_code=None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details


def error_body(code, message, details=None):
    return {"error": {"code": code, "message": message, "details": details, "request_id": get_request_id()}}


def api_exception_handler(exc, context):
    if isinstance(exc, ServiceError):
        return Response(error_body(exc.code, exc.message, exc.details), status=exc.status_code)
    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(
            exc.message_dict if hasattr(exc, "error_dict") else {"non_field_errors": exc.messages}
        )
    elif isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    response = exception_handler(exc, context)
    if response is None:
        return None  # Unhandled: Django returns 500 and logs it.

    if isinstance(exc, exceptions.ValidationError):
        code, message, details = "validation_error", "Invalid input.", response.data
    else:
        codes = exc.get_codes()
        code = codes if isinstance(codes, str) else "error"
        data = response.data
        message = str(data.get("detail", exc)) if isinstance(data, dict) else str(exc)
        details = None
    response.data = error_body(code, message, details)
    return response
