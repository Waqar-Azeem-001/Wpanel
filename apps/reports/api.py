"""
REST API for reports: the catalogue, and each report as JSON or as a CSV / XLSX / PDF download.

A report needs ``view_reports`` and the permission for the area it reads (a stranger gets 403, an unknown slug 404).
Use ``?export=csv|xlsx|pdf`` for a file (not ``format``, which DRF reserves); downloads are recorded in the audit log.
"""
from datetime import date, datetime
from decimal import Decimal

from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .builders import GROUP_TITLES


class ReportSummarySerializer(serializers.Serializer):
    slug = serializers.CharField()
    title = serializers.CharField()
    group = serializers.CharField()
    description = serializers.CharField()
    parameters = serializers.ListField(child=serializers.CharField(),
                                       help_text="Which of from, to, group, days and scope it accepts.")


class ColumnSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    kind = serializers.CharField(help_text="text, int, money, date, datetime, percent or hours.")


class FigureSerializer(serializers.Serializer):
    label = serializers.CharField()
    kind = serializers.CharField()
    value = serializers.JSONField()


class ReportResultSerializer(serializers.Serializer):
    slug = serializers.CharField()
    title = serializers.CharField()
    parameters = serializers.DictField(child=serializers.CharField())
    columns = ColumnSerializer(many=True)
    summary = FigureSerializer(many=True)
    rows = serializers.ListField(child=serializers.DictField())
    notes = serializers.ListField(child=serializers.CharField())
    truncated = serializers.BooleanField()


PARAMETERS = [
    OpenApiParameter("from", OpenApiTypes.DATE, description="First day (default: 30 days before `to`)."),
    OpenApiParameter("to", OpenApiTypes.DATE, description="Last day (default: today)."),
    OpenApiParameter("group", OpenApiTypes.STR, enum=["day", "month", "year"]),
    OpenApiParameter("days", OpenApiTypes.INT, description="For expiring services: how many days ahead."),
    OpenApiParameter("scope", OpenApiTypes.STR, enum=["all", "hosting", "domain"]),
    OpenApiParameter("export", OpenApiTypes.STR, enum=["csv", "xlsx", "pdf"],
                     description="Return a file instead of JSON."),
]


class ReportListView(APIView):
    """The reports the signed-in staff member may run."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=ReportSummarySerializer(many=True))
    def get(self, request):
        return Response(ReportSummarySerializer([{
            "slug": d.slug, "title": d.title, "group": GROUP_TITLES[d.group], "description": d.description,
            "parameters": _parameters(d)}
            for d in services.available(request.user)], many=True).data)


def _parameters(definition):
    names = []
    if "range" in definition.uses:
        names += ["from", "to"]
    names += [n for n in ("group", "days", "scope") if n in definition.uses]
    return names


def _json_value(value):
    """Money as an exact string ("80.00", never a float); times in ISO 8601 in the site's time zone."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, datetime):
        return (timezone.localtime(value) if timezone.is_aware(value) else value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


class ReportView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(parameters=PARAMETERS, responses={200: ReportResultSerializer,
                                                     (200, "application/pdf"): OpenApiTypes.BINARY})
    def get(self, request, slug):
        fmt = request.query_params.get("export", "")
        if fmt:
            content, content_type, filename = services.export(request.user, slug, request.query_params, fmt,
                                                              request=request)
            response = HttpResponse(content, content_type=content_type)
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            response["X-Content-Type-Options"] = "nosniff"
            response["Cache-Control"] = "private, no-store"
            return response
        _, _, result = services.run(request.user, slug, request.query_params)
        return Response(ReportResultSerializer({
            "slug": slug, "title": result.title, "parameters": result.params,
            "columns": [{"key": c.key, "label": c.label, "kind": c.kind} for c in result.columns],
            "summary": [{"label": label, "kind": kind, "value": _json_value(value)}
                        for label, kind, value in result.summary],
            "rows": [{c.key: _json_value(row.get(c.key)) for c in result.columns} for row in result.rows],
            "notes": result.notes, "truncated": result.truncated}).data)

