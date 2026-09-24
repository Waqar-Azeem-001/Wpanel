from django_filters import rest_framework as filters
from rest_framework import serializers, viewsets

from apps.accounts.roles import perm
from apps.core.permissions import HasPortalPermission

from .models import AuditEvent


class AuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEvent
        fields = ["id", "action", "actor", "actor_repr", "target_type", "target_id", "target_repr", "metadata",
                  "ip_address", "user_agent", "request_id", "created_at"]
        read_only_fields = fields


class AuditEventFilter(filters.FilterSet):
    action = filters.CharFilter(lookup_expr="istartswith")
    created_after = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lt")

    class Meta:
        model = AuditEvent
        fields = ["action", "actor", "target_type", "target_id", "request_id"]


class AuditEventViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditEvent.objects.all()
    serializer_class = AuditEventSerializer
    permission_classes = [HasPortalPermission]
    required_permissions = [perm("view_audit_log")]
    filterset_class = AuditEventFilter
    search_fields = ["action", "actor_repr", "target_repr"]
    ordering_fields = ["created_at"]
