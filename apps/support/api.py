"""
REST API for support: tickets, departments, predefined replies and the knowledgebase.

Object-level rules live in ``services``: a customer sees only their own clients' tickets and never an internal note;
staff need ``view_support`` to read and ``manage_support`` to act. Files are uploaded as ``multipart/form-data``
(field ``files``) and downloaded through an authenticated endpoint - never through a public URL.
"""
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.roles import perm
from apps.clients.models import Client
from apps.clients.services import single_contact_client
from apps.core.exceptions import ServiceError
from apps.core.permissions import HasPortalPermission
from apps.domains.models import Domain
from apps.hosting.models import HostingAccount

from . import kb, lifecycle, services
from .models import (CannedReply, Department, KBArticle, KBCategory, MessageKind, Ticket, TicketAttachment,
                     TicketMessage, TicketPriority, TicketStatus)


# --- Output ----------------------------------------------------------------------------------------------

class TicketAttachmentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = TicketAttachment
        fields = ["id", "original_name", "content_type", "size", "url"]
        read_only_fields = fields

    def get_url(self, attachment) -> str:
        return f"/api/v1/tickets/{attachment.message.ticket_id}/attachments/{attachment.pk}/"


class TicketMessageSerializer(serializers.ModelSerializer):
    kind = serializers.ChoiceField(choices=MessageKind.choices, read_only=True)
    attachments = TicketAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = TicketMessage
        fields = ["id", "kind", "author_name", "body", "is_internal", "attachments", "created_at"]
        read_only_fields = fields


class TicketSerializer(serializers.ModelSerializer):
    reference = serializers.CharField(read_only=True)
    status = serializers.ChoiceField(choices=TicketStatus.choices, read_only=True)
    priority = serializers.ChoiceField(choices=TicketPriority.choices, read_only=True)
    client_id = serializers.IntegerField(read_only=True)
    department = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    assigned_to = serializers.SerializerMethodField()
    hosting_account_id = serializers.IntegerField(read_only=True)
    domain_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Ticket
        fields = ["id", "reference", "client_id", "subject", "status", "priority", "department", "assigned_to",
                  "hosting_account_id", "domain_id", "last_activity_at", "last_customer_reply_at",
                  "last_staff_reply_at", "resolved_at", "closed_at", "created_at"]
        read_only_fields = fields

    def get_assigned_to(self, ticket) -> str | None:
        # Who is handling a ticket is shown to staff; a customer just sees that it has been picked up.
        request = self.context.get("request")
        if ticket.assigned_to is None:
            return None
        return ticket.assigned_to.email if request and services.can_view_all(request.user) else "support"


class TicketDetailSerializer(TicketSerializer):
    messages = serializers.SerializerMethodField()

    class Meta(TicketSerializer.Meta):
        fields = [*TicketSerializer.Meta.fields, "messages"]
        read_only_fields = fields

    def get_messages(self, ticket) -> list:
        rows = services.messages_for(self.context["request"].user, ticket)
        return TicketMessageSerializer(rows, many=True).data


class DepartmentSerializer(serializers.ModelSerializer):
    default_assignee = serializers.EmailField(source="default_assignee.email", read_only=True, default=None)
    default_assignee_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Department
        fields = ["id", "name", "slug", "description", "is_active", "sort_order", "default_assignee",
                  "default_assignee_id"]
        read_only_fields = ["id", "slug", "default_assignee"]


class CannedReplySerializer(serializers.ModelSerializer):
    department_id = serializers.PrimaryKeyRelatedField(source="department", queryset=Department.objects.all(),
                                                       allow_null=True, required=False)

    class Meta:
        model = CannedReply
        fields = ["id", "title", "body", "department_id", "is_active", "sort_order"]
        read_only_fields = ["id"]


class KBCategorySerializer(serializers.ModelSerializer):
    article_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = KBCategory
        fields = ["id", "name", "slug", "description", "sort_order", "is_active", "article_count"]
        read_only_fields = ["id", "slug", "article_count"]


class KBArticleSerializer(serializers.ModelSerializer):
    category = serializers.SlugRelatedField(slug_field="slug", queryset=KBCategory.objects.all())

    class Meta:
        model = KBArticle
        fields = ["id", "category", "title", "slug", "body", "is_published", "sort_order", "updated_at"]
        read_only_fields = ["id", "slug", "updated_at"]


# --- Input -----------------------------------------------------------------------------------------------

class TicketCreateSerializer(serializers.Serializer):
    client = serializers.IntegerField(min_value=1, required=False,
                                      help_text="Required for staff and for customers with more than one account.")
    department = serializers.SlugRelatedField(slug_field="slug", queryset=Department.objects.filter(is_active=True))
    subject = serializers.CharField(max_length=services.MAX_SUBJECT)
    body = serializers.CharField(max_length=services.MAX_BODY)
    priority = serializers.ChoiceField(choices=TicketPriority.choices, default=TicketPriority.NORMAL)
    hosting_account = serializers.PrimaryKeyRelatedField(queryset=HostingAccount.objects.all(), required=False,
                                                         allow_null=True)
    domain = serializers.PrimaryKeyRelatedField(queryset=Domain.objects.all(), required=False, allow_null=True)
    files = serializers.ListField(child=serializers.FileField(), required=False, max_length=5)


class TicketReplySerializer(serializers.Serializer):
    body = serializers.CharField(max_length=services.MAX_BODY)
    internal = serializers.BooleanField(required=False, default=False,
                                        help_text="Staff only: a note the customer never sees.")
    set_status = serializers.ChoiceField(choices=TicketStatus.choices, required=False,
                                         help_text="Staff only: the status to set after this message.")
    files = serializers.ListField(child=serializers.FileField(), required=False, max_length=5)


class StatusInSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=TicketStatus.choices)


class PriorityInSerializer(serializers.Serializer):
    priority = serializers.ChoiceField(choices=TicketPriority.choices)


class AssignInSerializer(serializers.Serializer):
    agent = serializers.IntegerField(allow_null=True, help_text="A staff user's id, or null to unassign.")


class DepartmentInSerializer(serializers.Serializer):
    department = serializers.SlugRelatedField(slug_field="slug", queryset=Department.objects.filter(is_active=True))


# --- Tickets --------------------------------------------------------------------------------------------------

class TicketFilter(filters.FilterSet):
    status = filters.CharFilter(method="filter_status", help_text="A status, or 'active' (not resolved or closed).")
    priority = filters.ChoiceFilter(choices=TicketPriority.choices)
    department = filters.CharFilter(field_name="department__slug")
    client = filters.NumberFilter(field_name="client_id")
    assigned = filters.ChoiceFilter(choices=[("me", "me"), ("none", "none")], method="filter_assigned")
    q = filters.CharFilter(method="filter_q")

    class Meta:
        model = Ticket
        fields = []

    def filter_status(self, queryset, name, value):
        if value == "active":
            return queryset.filter(status__in=lifecycle.ACTIVE)
        return queryset.filter(status=value) if value in TicketStatus.values else queryset.none()

    def filter_assigned(self, queryset, name, value):
        if value == "me":
            return queryset.filter(assigned_to=self.request.user)
        return queryset.filter(assigned_to__isnull=True)

    def filter_q(self, queryset, name, value):
        return services.search_tickets(queryset, value)


def _resolve_client(actor, client_id):
    """The client a new ticket is for. Someone else's client looks exactly like one that does not exist."""
    if client_id:
        client = Client.objects.filter(pk=client_id).first()
        if client is None or not (services.can_manage(actor) or services.is_client_contact(actor, client)):
            raise ServiceError("Client not found.", code="not_found", status_code=status.HTTP_404_NOT_FOUND)
        return client
    client = single_contact_client(actor)
    if client is None:
        raise ServiceError("Say which client this is for.", code="client_required")
    return client


class TicketViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                    viewsets.GenericViewSet):
    """
    Staff (``view_support``) see every ticket; customers see their own clients' tickets, without internal notes.
    Replying and closing are open to a ticket's contacts; status, assignment, priority and department to staff
    with ``manage_support``.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = TicketSerializer
    filterset_class = TicketFilter
    ordering_fields = ["last_activity_at", "created_at", "priority"]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Ticket.objects.none()
        return services.visible_tickets_for_user(self.request.user)

    def get_serializer_class(self):
        return TicketDetailSerializer if self.action == "retrieve" else TicketSerializer

    @extend_schema(request=TicketCreateSerializer, responses={201: TicketDetailSerializer})
    def create(self, request, *args, **kwargs):
        serializer = TicketCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        ticket = services.open_ticket(
            request.user, _resolve_client(request.user, data.get("client")), department=data["department"],
            subject=data["subject"], body=data["body"], priority=data["priority"],
            hosting_account=data.get("hosting_account"), domain=data.get("domain"), files=data.get("files", []),
            request=request)
        return Response(TicketDetailSerializer(ticket, context={"request": request}).data,
                        status=status.HTTP_201_CREATED)

    @extend_schema(request=TicketReplySerializer, responses={201: TicketMessageSerializer})
    @action(detail=True, methods=["post"])
    def reply(self, request, *args, **kwargs):
        serializer = TicketReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        message = services.reply(request.user, self.get_object(), data["body"], files=data.get("files", []),
                                 internal=data["internal"], set_status=data.get("set_status"), request=request)
        return Response(TicketMessageSerializer(message).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=TicketSerializer)
    @action(detail=True, methods=["post"])
    def close(self, request, *args, **kwargs):
        ticket = services.set_status(request.user, self.get_object(), TicketStatus.CLOSED, reason="closed",
                                     request=request)
        return Response(TicketSerializer(ticket, context={"request": request}).data)

    @extend_schema(request=StatusInSerializer, responses=TicketSerializer)
    @action(detail=True, methods=["post"], url_path="status")
    def set_status(self, request, *args, **kwargs):
        serializer = StatusInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = services.set_status(request.user, self.get_object(), serializer.validated_data["status"],
                                     reason="set by staff", request=request)
        return Response(TicketSerializer(ticket, context={"request": request}).data)

    @extend_schema(request=AssignInSerializer, responses=TicketSerializer)
    @action(detail=True, methods=["post"])
    def assign(self, request, *args, **kwargs):
        serializer = AssignInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agent_id = serializer.validated_data["agent"]
        ticket = self.get_object()
        agent = None
        if agent_id is not None:
            from apps.accounts.models import User

            agent = User.objects.filter(pk=agent_id).first()
            if agent is None:
                raise ServiceError("That person cannot handle tickets.", code="assignee_invalid")
        ticket = services.assign(request.user, ticket, agent, request=request)
        return Response(TicketSerializer(ticket, context={"request": request}).data)

    @extend_schema(request=PriorityInSerializer, responses=TicketSerializer)
    @action(detail=True, methods=["post"])
    def priority(self, request, *args, **kwargs):
        serializer = PriorityInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = services.set_priority(request.user, self.get_object(), serializer.validated_data["priority"],
                                       request=request)
        return Response(TicketSerializer(ticket, context={"request": request}).data)

    @extend_schema(request=DepartmentInSerializer, responses=TicketSerializer)
    @action(detail=True, methods=["post"], url_path="department")
    def move(self, request, *args, **kwargs):
        serializer = DepartmentInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = services.set_department(request.user, self.get_object(), serializer.validated_data["department"],
                                         request=request)
        return Response(TicketSerializer(ticket, context={"request": request}).data)

    @extend_schema(responses={(200, "application/octet-stream"): bytes})
    @action(detail=True, methods=["get"], url_path=r"attachments/(?P<attachment_id>\d+)", filter_backends=[])
    def attachment(self, request, attachment_id=None, *args, **kwargs):
        """Download one attachment (always as a download)."""
        ticket = self.get_object()
        item = get_object_or_404(TicketAttachment.objects.filter(message__ticket=ticket), pk=attachment_id)
        if not services.can_view_attachment(request.user, item):
            raise Http404
        response = FileResponse(item.file.open("rb"), as_attachment=True, filename=item.original_name,
                                content_type=item.content_type)
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response


# --- Departments, predefined replies -----------------------------------------------------------------------------

class DepartmentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                        mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Signed-in users see the active departments (to choose one); ``manage_support`` changes them."""

    permission_classes = [IsAuthenticated]
    serializer_class = DepartmentSerializer
    pagination_class = None
    http_method_names = ["get", "post", "put", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Department.objects.none()
        queryset = Department.objects.select_related("default_assignee")
        return queryset if services.can_view_all(self.request.user) else queryset.filter(is_active=True)

    def _save(self, request, instance):
        serializer = DepartmentSerializer(instance, data=request.data, partial=False)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        assignee = None
        if data.get("default_assignee_id"):
            from apps.accounts.models import User

            assignee = User.objects.filter(pk=data["default_assignee_id"]).first()
            if assignee is None:
                raise ServiceError("The default assignee cannot handle tickets.", code="assignee_invalid")
        department = services.save_department(
            request.user, instance, name=data["name"], description=data.get("description", ""),
            is_active=data.get("is_active", True), sort_order=data.get("sort_order", 0), default_assignee=assignee,
            request=request)
        return DepartmentSerializer(department).data

    @extend_schema(request=DepartmentSerializer, responses={201: DepartmentSerializer})
    def create(self, request, *args, **kwargs):
        return Response(self._save(request, None), status=status.HTTP_201_CREATED)

    @extend_schema(request=DepartmentSerializer, responses=DepartmentSerializer)
    def update(self, request, *args, **kwargs):
        return Response(self._save(request, self.get_object()))


class CannedReplyViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                         mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Predefined replies: staff only (``view_support`` to read, ``manage_support`` to change)."""

    permission_classes = [HasPortalPermission]
    serializer_class = CannedReplySerializer
    queryset = CannedReply.objects.select_related("department")
    pagination_class = None
    http_method_names = ["get", "post", "put", "head", "options"]

    @property
    def required_permissions(self):
        return [perm("view_support")] if self.request.method in ("GET", "HEAD", "OPTIONS") else [perm("manage_support")]

    def _save(self, request, instance):
        serializer = CannedReplySerializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        reply = services.save_canned_reply(request.user, instance, title=data["title"], body=data["body"],
                                           department=data.get("department"), is_active=data.get("is_active", True),
                                           sort_order=data.get("sort_order", 0), request=request)
        return CannedReplySerializer(reply).data

    @extend_schema(request=CannedReplySerializer, responses={201: CannedReplySerializer})
    def create(self, request, *args, **kwargs):
        return Response(self._save(request, None), status=status.HTTP_201_CREATED)

    @extend_schema(request=CannedReplySerializer, responses=CannedReplySerializer)
    def update(self, request, *args, **kwargs):
        return Response(self._save(request, self.get_object()))


# --- Knowledgebase ---------------------------------------------------------------------------------------------------

class KBPermission(HasPortalPermission):
    """Anyone may read (published articles only - see the queryset); ``manage_support`` writes."""

    def has_permission(self, request, view):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        return super().has_permission(request, view)


class KBCategoryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                        mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [KBPermission]
    required_permissions = [perm("manage_support")]
    serializer_class = KBCategorySerializer
    lookup_field = "slug"
    pagination_class = None
    http_method_names = ["get", "post", "put", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return KBCategory.objects.none()
        user = self.request.user
        return KBCategory.objects.all() if services.can_view_all(user) else kb.public_categories()

    def _save(self, request, instance):
        serializer = KBCategorySerializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        category = kb.save_category(request.user, instance, name=data["name"], description=data.get("description", ""),
                                    sort_order=data.get("sort_order", 0), is_active=data.get("is_active", True),
                                    request=request)
        return KBCategorySerializer(category).data

    @extend_schema(request=KBCategorySerializer, responses={201: KBCategorySerializer})
    def create(self, request, *args, **kwargs):
        return Response(self._save(request, None), status=status.HTTP_201_CREATED)

    @extend_schema(request=KBCategorySerializer, responses=KBCategorySerializer)
    def update(self, request, *args, **kwargs):
        return Response(self._save(request, self.get_object()))


class KBArticleFilter(filters.FilterSet):
    category = filters.CharFilter(field_name="category__slug")
    q = filters.CharFilter(method="filter_q")

    class Meta:
        model = KBArticle
        fields = []

    def filter_q(self, queryset, name, value):
        return queryset.filter(pk__in=kb.search_public(value).values("pk")) if not services.can_view_all(
            self.request.user) else queryset.filter(title__icontains=value) | queryset.filter(body__icontains=value)


class KBArticleViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                       mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    permission_classes = [KBPermission]
    required_permissions = [perm("manage_support")]
    serializer_class = KBArticleSerializer
    filterset_class = KBArticleFilter
    http_method_names = ["get", "post", "put", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return KBArticle.objects.none()
        user = self.request.user
        if services.can_view_all(user):
            return KBArticle.objects.select_related("category")
        return kb.public_articles()

    def _save(self, request, instance):
        serializer = KBArticleSerializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        article = kb.save_article(request.user, instance, category=data["category"], title=data["title"],
                                  body=data["body"], is_published=data.get("is_published", False),
                                  sort_order=data.get("sort_order", 0), request=request)
        return KBArticleSerializer(article).data

    @extend_schema(request=KBArticleSerializer, responses={201: KBArticleSerializer})
    def create(self, request, *args, **kwargs):
        return Response(self._save(request, None), status=status.HTTP_201_CREATED)

    @extend_schema(request=KBArticleSerializer, responses=KBArticleSerializer)
    def update(self, request, *args, **kwargs):
        return Response(self._save(request, self.get_object()))

    def destroy(self, request, *args, **kwargs):
        kb.delete_article(request.user, self.get_object(), request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

