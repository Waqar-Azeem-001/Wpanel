import logging

from django.core.cache import cache
from django.db import connection
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class HealthView(APIView):
    """Readiness check for load balancers and monitoring."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    @extend_schema(
        responses=inline_serializer("Health", {"status": serializers.CharField(), "checks": serializers.DictField()})
    )
    def get(self, request, *args, **kwargs):
        checks = {}
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            checks["database"] = "ok"
        except Exception:
            logger.exception("Health check: database unavailable")
            checks["database"] = "error"
        try:
            cache.set("health:ping", "1", 5)
            checks["cache"] = "ok" if cache.get("health:ping") == "1" else "error"
        except Exception:
            logger.exception("Health check: cache unavailable")
            checks["cache"] = "error"
        healthy = all(v == "ok" for v in checks.values())
        return Response({"status": "ok" if healthy else "degraded", "checks": checks}, status=200 if healthy else 503)
