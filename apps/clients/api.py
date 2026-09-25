from django.shortcuts import get_object_or_404
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response

from apps.accounts.roles import perm
from apps.audit.api import AuditEventSerializer
from apps.core.permissions import HasPortalPermission

from . import services
from .models import Client, ClientContact, ClientStatus, ContactRole


class ContactSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    name = serializers.CharField(source="user.full_name", read_only=True)
    user_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = ClientContact
        fields = ["id", "user_id", "email", "name", "role", "created_at"]
        read_only_fields = fields


_CLIENT_FIELDS = ["id", "reference", "display_name", "company_name", "first_name", "last_name", "email", "phone",
                  "address_line1", "address_line2", "city", "state", "postcode", "country", "tax_id", "currency",
                  "status", "contacts", "created_at", "updated_at"]


class UpperCaseCharField(serializers.CharField):
    """Country/currency codes are accepted in any case and stored upper-case."""

    def to_internal_value(self, data):
        return super().to_internal_value(data).upper()


class ClientSerializer(serializers.ModelSerializer):
    """Staff view of a client (includes internal notes)."""


    reference = serializers.CharField(read_only=True)
    display_name = serializers.CharField(read_only=True)
    contacts = ContactSerializer(many=True, read_only=True)

    def build_standard_field(self, field_name, model_field):
        field_class, kwargs = super().build_standard_field(field_name, model_field)
        if field_name in ("country", "currency"):
            field_class = UpperCaseCharField
        return field_class, kwargs

    class Meta:
        model = Client
        fields = [*_CLIENT_FIELDS, "tax_exempt", "notes"]
        read_only_fields = ["id", "reference", "display_name", "status", "contacts", "created_at", "updated_at"]


class ClientCreateSerializer(ClientSerializer):
    owner_email = serializers.EmailField(required=False, write_only=True,
                                         help_text="Owner contact. Defaults to the client email.")

    class Meta(ClientSerializer.Meta):
        fields = [*ClientSerializer.Meta.fields, "owner_email"]


class CustomerClientSerializer(ClientSerializer):
    """A contact's view of their own client (no internal notes, no currency changes)."""

    my_role = serializers.SerializerMethodField()

    class Meta(ClientSerializer.Meta):
        fields = [*_CLIENT_FIELDS, "my_role"]
        read_only_fields = [*ClientSerializer.Meta.read_only_fields, "currency", "my_role"]

    def get_my_role(self, client) -> str | None:
        return services.contact_role(self.context["request"].user, client)


class SetClientStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=ClientStatus.choices)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class AddContactSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=ContactRole.choices, default=ContactRole.TECHNICAL)
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=150)


class ContactRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=ContactRole.choices)


class ClientFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search", label="Name, company, email, phone, reference or contact")
    created_after = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lt")

    class Meta:
        model = Client
        fields = ["status", "country", "currency"]

    def filter_search(self, queryset, name, value):
        return services.search_clients(queryset, value)


class ClientViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                    mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Staff client management."""

    queryset = Client.objects.prefetch_related("contacts__user")
    permission_classes = [HasPortalPermission]
    filterset_class = ClientFilter
    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ["created_at", "company_name", "last_name", "email"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    @property
    def required_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [perm("view_clients")]
        return [perm("manage_clients")]

    def get_serializer_class(self):
        return ClientCreateSerializer if self.action == "create" else ClientSerializer

    def create(self, request, *args, **kwargs):
        serializer = ClientCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        owner_email = data.pop("owner_email", None)
        client = services.create_client(request.user, data, owner_email=owner_email, request=request)
        return Response(ClientSerializer(client).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        services.update_client(self.request.user, serializer.instance, serializer.validated_data,
                               request=self.request)

    @extend_schema(request=SetClientStatusSerializer, responses=ClientSerializer)
    @action(detail=True, methods=["post"], url_path="status")
    def set_status(self, request, *args, **kwargs):
        serializer = SetClientStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        client = services.set_client_status(request.user, self.get_object(), serializer.validated_data["status"],
                                            reason=serializer.validated_data.get("reason", ""), request=request)
        return Response(ClientSerializer(client).data)

    @extend_schema(request=AddContactSerializer, responses={201: ContactSerializer})
    @action(detail=True, methods=["post"], url_path="contacts")
    def add_contact(self, request, *args, **kwargs):
        serializer = AddContactSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contact = services.add_contact(request.user, self.get_object(), request=request,
                                       **serializer.validated_data)
        return Response(ContactSerializer(contact).data, status=status.HTTP_201_CREATED)

    def _contact(self, contact_id):
        return get_object_or_404(ClientContact.objects.select_related("user", "client"),
                                 pk=contact_id, client=self.get_object())

    @extend_schema(request=ContactRoleSerializer, responses=ContactSerializer)
    @action(detail=True, methods=["patch", "delete"], url_path=r"contacts/(?P<contact_id>\d+)")
    def contact(self, request, contact_id=None, *args, **kwargs):
        contact = self._contact(contact_id)
        if request.method == "DELETE":
            services.remove_contact(request.user, contact, request=request)
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = ContactRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contact = services.change_contact_role(request.user, contact, serializer.validated_data["role"],
                                               request=request)
        return Response(ContactSerializer(contact).data)

    @extend_schema(responses=AuditEventSerializer(many=True))
    @action(detail=True, methods=["get"])
    def activity(self, request, *args, **kwargs):
        page = self.paginate_queryset(services.client_activity(self.get_object()))
        return self.get_paginated_response(AuditEventSerializer(page, many=True).data)


class MyClientViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                      viewsets.GenericViewSet):
    """The client accounts the signed-in user is a contact of."""

    serializer_class = CustomerClientSerializer
    http_method_names = ["get", "patch", "head", "options"]
    filter_backends = []

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Client.objects.none()
        # Contacts only - staff use /clients/ for other accounts.
        return Client.objects.filter(contacts__user=self.request.user).prefetch_related("contacts__user")

    def perform_update(self, serializer):
        services.update_client(self.request.user, serializer.instance, serializer.validated_data,
                               request=self.request)
