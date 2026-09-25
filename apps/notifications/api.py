from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.roles import perm
from apps.core.permissions import HasPortalPermission

from . import services
from .models import EmailMessage, Notification


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


class UnreadCountSerializer(serializers.Serializer):
    unread = serializers.IntegerField()


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

    @extend_schema(responses=UnreadCountSerializer)
    @action(detail=False, methods=["get"], url_path="unread-count", filter_backends=[], pagination_class=None)
    def unread_count(self, request, *args, **kwargs):
        return Response({"unread": services.unread_count(request.user)})


class PreferenceSerializer(serializers.Serializer):
    category = serializers.CharField(read_only=True)
    label = serializers.CharField(read_only=True)
    email_enabled = serializers.BooleanField()
    in_app_enabled = serializers.BooleanField()


class PreferencesView(APIView):
    """
    The signed-in user's choices for optional messages. Account, billing and service-continuity messages are always sent
    and are not listed here.
    """

    permission_classes = [IsAuthenticated]

    def _rows(self, request):
        return [{"category": r["category"], "label": r["label"], "email_enabled": r["email_enabled"],
                 "in_app_enabled": r["in_app_enabled"]} for r in services.preference_rows(request.user)]

    @extend_schema(responses=PreferenceSerializer(many=True))
    def get(self, request):
        return Response(PreferenceSerializer(self._rows(request), many=True).data)

    @extend_schema(request=PreferenceSerializer(many=True), responses=PreferenceSerializer(many=True))
    def put(self, request):
        serializer = PreferenceSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        choices = {}
        for row, submitted in zip(request.data, serializer.validated_data):
            choices[str(row.get("category", ""))] = (submitted["email_enabled"], submitted["in_app_enabled"])
        services.save_preferences(request.user, choices)
        return Response(PreferenceSerializer(self._rows(request), many=True).data)


class EmailMessageSerializer(serializers.ModelSerializer):
    status = serializers.ChoiceField(choices=EmailMessage.Status.choices, read_only=True)
    event_label = serializers.CharField(read_only=True)
    body = serializers.CharField(source="body_text", read_only=True)

    class Meta:
        model = EmailMessage
        fields = ["id", "event", "event_label", "template", "to_email", "subject", "body", "status", "attempts",
                  "last_error", "sent_at", "opened_at", "open_count", "is_sensitive", "created_at"]
        read_only_fields = fields


class EmailStatsSerializer(serializers.Serializer):
    days = serializers.IntegerField()
    total = serializers.IntegerField()
    sent = serializers.IntegerField()
    failed = serializers.IntegerField()
    queued = serializers.IntegerField()
    opened = serializers.IntegerField()
    open_rate = serializers.FloatField(allow_null=True, help_text="A signal, not proof of reading.")
    tracking_enabled = serializers.BooleanField()
    by_event = serializers.ListField(child=serializers.DictField())


class EmailMessageViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """The email log (``view_settings``); ``resend`` needs ``manage_settings``. Secret content is never stored here."""

    permission_classes = [HasPortalPermission]
    serializer_class = EmailMessageSerializer
    queryset = EmailMessage.objects.all()
    filterset_fields = ["status", "event"]
    search_fields = ["to_email", "subject"]
    ordering_fields = ["created_at"]

    @property
    def required_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [perm("view_settings")]
        return [perm("manage_settings")]

    @extend_schema(responses=EmailStatsSerializer)
    @action(detail=False, methods=["get"], filter_backends=[], pagination_class=None)
    def stats(self, request):
        try:
            days = int(request.query_params.get("days", 30))
        except ValueError:
            days = 30
        return Response(EmailStatsSerializer(services.email_stats(days=days if days in (7, 30, 90) else 30)).data)

    @extend_schema(request=None, responses=EmailMessageSerializer)
    @action(detail=True, methods=["post"])
    def resend(self, request, *args, **kwargs):
        message = services.resend(request.user, self.get_object(), request=request)
        return Response(EmailMessageSerializer(message).data)
