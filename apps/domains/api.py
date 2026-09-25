from django.shortcuts import get_object_or_404
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.roles import perm
from apps.clients.models import Client
from apps.lifecycle.serializers import LifecycleStageMixin
from apps.core.permissions import PublicReadPermission

from . import services
from .models import Domain, DomainStatus, DnsRecord, DnsRecordType, TldPricing


# --- TLD pricing --------------------------------------------------------------------------

class TldPricingSerializer(serializers.ModelSerializer):
    class Meta:
        model = TldPricing
        fields = ["id", "tld", "register_price", "renew_price", "transfer_price", "redemption_price",
                  "min_years", "max_years", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class PublicTldPricingSerializer(serializers.ModelSerializer):
    class Meta:
        model = TldPricing
        fields = ["tld", "register_price", "renew_price", "transfer_price", "min_years", "max_years"]
        read_only_fields = fields


class SetTldActiveSerializer(serializers.Serializer):
    is_active = serializers.BooleanField()


class TldPricingViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                        mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [PublicReadPermission]
    required_permissions = {"*": [perm("manage_domains")]}
    lookup_field = "tld"
    lookup_value_regex = r"\.[a-z0-9.]+"

    def get_queryset(self):
        return services.visible_tlds_for_user(self.request.user)

    def get_serializer_class(self):
        if self.request.user.is_authenticated and self.request.user.has_perm(perm("view_domains")):
            return TldPricingSerializer
        return PublicTldPricingSerializer

    def create(self, request, *args, **kwargs):
        serializer = TldPricingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        tld = data.pop("tld")
        pricing = services.set_tld_pricing(request.user, tld, request=request, **data)
        return Response(TldPricingSerializer(pricing).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        data = dict(serializer.validated_data)
        data.pop("tld", None)
        services.set_tld_pricing(self.request.user, serializer.instance.tld, request=self.request, **{
            "register_price": data.get("register_price", serializer.instance.register_price),
            "renew_price": data.get("renew_price", serializer.instance.renew_price),
            "transfer_price": data.get("transfer_price", serializer.instance.transfer_price),
            "redemption_price": data.get("redemption_price", serializer.instance.redemption_price),
            "min_years": data.get("min_years", serializer.instance.min_years),
            "max_years": data.get("max_years", serializer.instance.max_years),
        })

    @extend_schema(request=SetTldActiveSerializer, responses=TldPricingSerializer)
    @action(detail=True, methods=["post"], url_path="status")
    def set_status(self, request, *args, **kwargs):
        serializer = SetTldActiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pricing = services.set_tld_pricing_active(request.user, self.get_object(),
                                                  serializer.validated_data["is_active"], request=request)
        return Response(TldPricingSerializer(pricing).data)


# --- Availability -------------------------------------------------------------------------

class AvailabilityQuerySerializer(serializers.Serializer):
    domain = serializers.CharField()
    years = serializers.IntegerField(required=False, default=1, min_value=1)


class AvailabilityView(APIView):
    """Public: search whether a domain name can be registered, with its pricing."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        parameters=[AvailabilityQuerySerializer],
        responses=inline_serializer("Availability", {
            "domain": serializers.CharField(), "available": serializers.BooleanField(),
            "tld": serializers.CharField(), "register_price": serializers.DecimalField(10, 2),
            "renew_price": serializers.DecimalField(10, 2), "transfer_price": serializers.DecimalField(10, 2),
            "years": serializers.IntegerField(),
        }),
    )
    def get(self, request, *args, **kwargs):
        query = AvailabilityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        available, pricing = services.check_availability(query.validated_data["domain"])
        return Response({
            "domain": query.validated_data["domain"].strip().lower(), "available": available, "tld": pricing.tld,
            # Stringified like every other DecimalField in the API (DRF's JSON renderer would
            # otherwise emit these as floats, inconsistent with PriceSerializer elsewhere).
            "register_price": str(pricing.register_price), "renew_price": str(pricing.renew_price),
            "transfer_price": str(pricing.transfer_price), "years": query.validated_data["years"],
        })


# --- Domains ------------------------------------------------------------------------------

class DnsRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = DnsRecord
        fields = ["id", "record_type", "name", "content", "ttl", "priority"]
        read_only_fields = ["id"]


class DomainSerializer(LifecycleStageMixin, serializers.ModelSerializer):
    dns_records = DnsRecordSerializer(many=True, read_only=True)
    client_name = serializers.CharField(source="client.display_name", read_only=True)

    class Meta:
        model = Domain
        fields = ["id", "name", "tld", "client", "client_name", "status", "years", "registered_at", "expires_at",
                  "auto_renew", "is_locked", "nameservers", "provider_ref", "last_error", "dns_records",
                  "created_at", "updated_at", "lifecycle_stage"]
        read_only_fields = ["id", "tld", "status", "registered_at", "expires_at", "provider_ref", "last_error",
                            "dns_records", "created_at", "updated_at"]


class RegisterDomainSerializer(serializers.Serializer):
    client_id = serializers.PrimaryKeyRelatedField(source="client", queryset=Client.objects.all())
    domain = serializers.CharField()
    years = serializers.IntegerField(min_value=1, default=1)
    nameservers = serializers.ListField(child=serializers.CharField(), required=False)


class TransferDomainSerializer(serializers.Serializer):
    client_id = serializers.PrimaryKeyRelatedField(source="client", queryset=Client.objects.all())
    domain = serializers.CharField()
    auth_code = serializers.CharField()
    years = serializers.IntegerField(min_value=1, default=1)


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class RenewSerializer(serializers.Serializer):
    years = serializers.IntegerField(min_value=1)


class AutoRenewSerializer(serializers.Serializer):
    auto_renew = serializers.BooleanField()


class NameserversSerializer(serializers.Serializer):
    nameservers = serializers.ListField(child=serializers.CharField(), min_length=2, max_length=13)


class DomainFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")

    class Meta:
        model = Domain
        fields = ["status", "tld", "client"]

    def filter_search(self, queryset, name, value):
        return services.search_domains(queryset, value)


class DomainViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    Staff (``view_domains``) see every domain; everyone else sees only domains
    belonging to a client they are a contact of (``get_queryset``).

    Unlike most viewsets in this project, authorization for every action below
    is decided in the service layer, not by a DRF permission class: some
    actions are staff-only (``complete``, ``cancel``, ``renew``, ``sync`` -
    ``manage_domains``) and others are shared self-service (``nameservers``,
    ``auto_renew``, ``lock``/``unlock``, DNS - ``manage_domains`` OR any
    contact of the domain's client), and a flat per-HTTP-method permission
    class can't express "OR ownership". Each service function raises
    ``ServiceError`` (403) when the actor isn't allowed; this view only
    requires that the caller be authenticated at all.
    """

    serializer_class = DomainSerializer
    permission_classes = [IsAuthenticated]
    filterset_class = DomainFilter
    ordering_fields = ["name", "expires_at", "created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Domain.objects.none()
        return services.visible_domains_for_user(self.request.user)

    @extend_schema(request=RegisterDomainSerializer, responses={201: DomainSerializer})
    @action(detail=False, methods=["post"])
    def register(self, request, *args, **kwargs):
        serializer = RegisterDomainSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        domain = services.request_registration(request.user, data.pop("client"), data.pop("domain"),
                                               data.pop("years"), data.get("nameservers"), request=request)
        return Response(DomainSerializer(domain).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=TransferDomainSerializer, responses={201: DomainSerializer})
    @action(detail=False, methods=["post"])
    def transfer(self, request, *args, **kwargs):
        serializer = TransferDomainSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        domain = services.request_transfer_in(request.user, data["client"], data["domain"], data["auth_code"],
                                              data["years"], request=request)
        return Response(DomainSerializer(domain).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=DomainSerializer)
    @action(detail=True, methods=["post"])
    def complete(self, request, *args, **kwargs):
        domain = self.get_object()
        if domain.status == DomainStatus.PENDING_TRANSFER_IN:
            domain = services.complete_transfer(request.user, domain, request=request)
        else:
            domain = services.complete_registration(request.user, domain, request=request)
        return Response(DomainSerializer(domain).data)

    @extend_schema(request=CancelSerializer, responses=DomainSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, *args, **kwargs):
        serializer = CancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        domain = services.cancel_domain_request(request.user, self.get_object(),
                                                reason=serializer.validated_data.get("reason", ""), request=request)
        return Response(DomainSerializer(domain).data)

    @extend_schema(request=RenewSerializer, responses=DomainSerializer)
    @action(detail=True, methods=["post"])
    def renew(self, request, *args, **kwargs):
        serializer = RenewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        domain = services.renew_domain(request.user, self.get_object(), serializer.validated_data["years"],
                                       request=request)
        return Response(DomainSerializer(domain).data)

    @extend_schema(request=AutoRenewSerializer, responses=DomainSerializer)
    @action(detail=True, methods=["post"], url_path="auto-renew")
    def auto_renew(self, request, *args, **kwargs):
        serializer = AutoRenewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        domain = services.set_auto_renew(request.user, self.get_object(), serializer.validated_data["auto_renew"],
                                         request=request)
        return Response(DomainSerializer(domain).data)

    @extend_schema(request=NameserversSerializer, responses=DomainSerializer)
    @action(detail=True, methods=["post"])
    def nameservers(self, request, *args, **kwargs):
        serializer = NameserversSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        domain = services.update_nameservers(request.user, self.get_object(),
                                             serializer.validated_data["nameservers"], request=request)
        return Response(DomainSerializer(domain).data)

    @extend_schema(request=None, responses=DomainSerializer)
    @action(detail=True, methods=["post"])
    def lock(self, request, *args, **kwargs):
        return Response(DomainSerializer(services.lock_domain(request.user, self.get_object(),
                                                              request=request)).data)

    @extend_schema(request=None, responses=DomainSerializer)
    @action(detail=True, methods=["post"])
    def unlock(self, request, *args, **kwargs):
        return Response(DomainSerializer(services.unlock_domain(request.user, self.get_object(),
                                                                request=request)).data)

    @extend_schema(request=None, responses=DomainSerializer)
    @action(detail=True, methods=["post"])
    def sync(self, request, *args, **kwargs):
        return Response(DomainSerializer(services.sync_domain(request.user, self.get_object(),
                                                              request=request)).data)

    @extend_schema(request=DnsRecordSerializer, responses={200: DnsRecordSerializer(many=True),
                                                          201: DnsRecordSerializer})
    @action(detail=True, methods=["get", "post"])
    def dns(self, request, *args, **kwargs):
        domain = self.get_object()
        if request.method == "GET":
            return Response(DnsRecordSerializer(domain.dns_records.all(), many=True).data)
        serializer = DnsRecordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = services.add_dns_record(request.user, domain, serializer.validated_data, request=request)
        return Response(DnsRecordSerializer(record).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=DnsRecordSerializer, responses=DnsRecordSerializer)
    @action(detail=True, methods=["patch", "delete"], url_path=r"dns/(?P<record_id>\d+)")
    def dns_detail(self, request, record_id=None, *args, **kwargs):
        domain = self.get_object()
        record = get_object_or_404(DnsRecord, pk=record_id, domain=domain)
        if request.method == "DELETE":
            services.delete_dns_record(request.user, domain, record, request=request)
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = DnsRecordSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        record = services.update_dns_record(request.user, domain, record, serializer.validated_data, request=request)
        return Response(DnsRecordSerializer(record).data)
