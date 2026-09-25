from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.clients.models import Client
from apps.lifecycle.serializers import LifecycleStageMixin
from apps.products.models import Product, Server

from . import services
from .models import HostingAccount


class HostingAccountSerializer(LifecycleStageMixin, serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.display_name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    server_name = serializers.CharField(source="server.name", read_only=True, default=None)

    class Meta:
        model = HostingAccount
        fields = ["id", "client", "client_name", "product", "product_name", "server", "server_name", "domain",
                  "username", "status", "package_name", "suspend_reason", "last_error", "last_synced_at",
                  "disk_used_mb", "disk_limit_mb", "bandwidth_used_mb", "bandwidth_limit_mb", "created_at",
                  "updated_at", "lifecycle_stage"]
        read_only_fields = ["id", "username", "status", "package_name", "suspend_reason", "last_error",
                            "last_synced_at", "disk_used_mb", "disk_limit_mb", "bandwidth_used_mb",
                            "bandwidth_limit_mb", "created_at", "updated_at"]


class RequestHostingSerializer(serializers.Serializer):
    client_id = serializers.PrimaryKeyRelatedField(source="client", queryset=Client.objects.all())
    product_id = serializers.PrimaryKeyRelatedField(source="product", queryset=Product.objects.all())
    domain = serializers.CharField()
    server_id = serializers.PrimaryKeyRelatedField(source="server", queryset=Server.objects.all(), required=False)


class AssignServerSerializer(serializers.Serializer):
    server_id = serializers.PrimaryKeyRelatedField(source="server", queryset=Server.objects.all())


class CancelHostingSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class SuspendSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class TerminateSerializer(serializers.Serializer):
    keep_dns = serializers.BooleanField(required=False, default=False)


class ChangePackageSerializer(serializers.Serializer):
    product_id = serializers.PrimaryKeyRelatedField(source="product", queryset=Product.objects.all())


class HostingAccountFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")

    class Meta:
        model = HostingAccount
        fields = ["status", "client", "server"]

    def filter_search(self, queryset, name, value):
        return services.search_hosting_accounts(queryset, value)


class HostingAccountViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    Staff (``view_hosting``) see every account; everyone else sees only
    accounts belonging to a client they are a contact of. As with
    ``apps.domains.api.DomainViewSet``, authorization for each action lives in
    the service layer (some actions are staff-only, ``request`` is shared) so
    this view only requires the caller be authenticated.
    """

    serializer_class = HostingAccountSerializer
    permission_classes = [IsAuthenticated]
    filterset_class = HostingAccountFilter
    ordering_fields = ["domain", "created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return HostingAccount.objects.none()
        return services.visible_hosting_accounts_for_user(self.request.user)

    @extend_schema(request=RequestHostingSerializer, responses={201: HostingAccountSerializer})
    @action(detail=False, methods=["post"])
    def request_account(self, request, *args, **kwargs):
        serializer = RequestHostingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        account = services.request_hosting(request.user, data.pop("client"), data.pop("product"),
                                           data.pop("domain"), server=data.get("server"), request=request)
        return Response(HostingAccountSerializer(account).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=AssignServerSerializer, responses=HostingAccountSerializer)
    @action(detail=True, methods=["post"], url_path="assign-server")
    def assign_server(self, request, *args, **kwargs):
        serializer = AssignServerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = services.assign_server(request.user, self.get_object(), serializer.validated_data["server"],
                                         request=request)
        return Response(HostingAccountSerializer(account).data)

    @extend_schema(request=None, responses=HostingAccountSerializer)
    @action(detail=True, methods=["post"])
    def complete(self, request, *args, **kwargs):
        account = services.complete_provisioning(request.user, self.get_object(), request=request)
        return Response(HostingAccountSerializer(account).data)

    @extend_schema(request=CancelHostingSerializer, responses=HostingAccountSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, *args, **kwargs):
        serializer = CancelHostingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = services.cancel_request(request.user, self.get_object(),
                                          reason=serializer.validated_data.get("reason", ""), request=request)
        return Response(HostingAccountSerializer(account).data)

    @extend_schema(request=SuspendSerializer, responses=HostingAccountSerializer)
    @action(detail=True, methods=["post"])
    def suspend(self, request, *args, **kwargs):
        serializer = SuspendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = services.suspend_account(request.user, self.get_object(),
                                           reason=serializer.validated_data.get("reason", ""), request=request)
        return Response(HostingAccountSerializer(account).data)

    @extend_schema(request=None, responses=HostingAccountSerializer)
    @action(detail=True, methods=["post"])
    def unsuspend(self, request, *args, **kwargs):
        account = services.unsuspend_account(request.user, self.get_object(), request=request)
        return Response(HostingAccountSerializer(account).data)

    @extend_schema(request=TerminateSerializer, responses=HostingAccountSerializer)
    @action(detail=True, methods=["post"])
    def terminate(self, request, *args, **kwargs):
        serializer = TerminateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = services.terminate_account(request.user, self.get_object(),
                                             keep_dns=serializer.validated_data["keep_dns"], request=request)
        return Response(HostingAccountSerializer(account).data)

    @extend_schema(request=ChangePackageSerializer, responses=HostingAccountSerializer)
    @action(detail=True, methods=["post"], url_path="change-package")
    def change_package(self, request, *args, **kwargs):
        serializer = ChangePackageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = services.change_package(request.user, self.get_object(), serializer.validated_data["product"],
                                          request=request)
        return Response(HostingAccountSerializer(account).data)

    @extend_schema(request=None, responses=HostingAccountSerializer)
    @action(detail=True, methods=["post"], url_path="sync-status")
    def sync_status(self, request, *args, **kwargs):
        account = services.sync_status(request.user, self.get_object(), request=request)
        return Response(HostingAccountSerializer(account).data)

    @extend_schema(request=None, responses=HostingAccountSerializer)
    @action(detail=True, methods=["post"], url_path="sync-usage")
    def sync_usage(self, request, *args, **kwargs):
        account = services.sync_usage(request.user, self.get_object(), request=request)
        return Response(HostingAccountSerializer(account).data)
