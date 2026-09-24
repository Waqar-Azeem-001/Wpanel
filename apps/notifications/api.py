from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from . import services
from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = ["id", "event", "title", "body", "link", "is_read", "read_at", "created_at"]
        read_only_fields = fields


class NotificationFilter(filters.FilterSet):
    unread = filters.BooleanFilter(field_name="read_at", lookup_expr="isnull")

    class Meta:
        model = Notification
        fields = ["event", "unread"]


class MarkReadSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(), required=False)


class NotificationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """The authenticated user's own notifications."""

    serializer_class = NotificationSerializer
    filterset_class = NotificationFilter
    ordering_fields = ["created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()
        return Notification.objects.filter(user=self.request.user)

    @extend_schema(
        request=MarkReadSerializer,
        responses=inline_serializer("MarkReadResult", {"updated": serializers.IntegerField()}),
    )
    @action(detail=False, methods=["post"], url_path="mark-read")
    def mark_read(self, request, *args, **kwargs):
        serializer = MarkReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = services.mark_read(request.user, serializer.validated_data.get("ids"))
        return Response({"updated": updated})
