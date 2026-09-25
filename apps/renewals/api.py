"""
REST API for renewals and upgrades. The only inputs are *which* service and *which* plan / how many
years - never a price, credit, expiry or total (unknown fields are ignored, and the calculation is
always redone on the server from the stored term).
"""
from django.shortcuts import get_object_or_404
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing import invoicing
from apps.domains import services as domain_services
from apps.hosting import services as hosting_services
from apps.products.models import BillingCycle, Product

from . import services
from .models import ChangeKind, ChangeStatus, ServiceChange
from .services import RECURRING_CYCLES


class ServiceChangeSerializer(serializers.ModelSerializer):
    kind = serializers.ChoiceField(choices=ChangeKind.choices, read_only=True)
    status = serializers.ChoiceField(choices=ChangeStatus.choices, read_only=True)
    invoice_id = serializers.IntegerField(read_only=True)
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    hosting_account_id = serializers.IntegerField(read_only=True)
    domain_id = serializers.IntegerField(read_only=True)
    error = serializers.SerializerMethodField()

    class Meta:
        model = ServiceChange
        fields = ["id", "kind", "status", "invoice_id", "invoice_number", "hosting_account_id", "domain_id",
                  "period_months", "paid_value", "calculation", "applied_at", "error", "created_at"]
        read_only_fields = fields

    def get_error(self, change) -> str:
        # The reason a paid change failed is an operational detail: staff see it, customers do not.
        request = self.context.get("request")
        return change.error if request and invoicing.is_staff_biller(request.user) else ""


class UpgradeOptionSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    product = serializers.CharField()
    billing_cycle = serializers.CharField()
    months = serializers.IntegerField()
    new_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    term_paid = serializers.DecimalField(max_digits=12, decimal_places=2)
    term_days = serializers.IntegerField()
    remaining_days = serializers.IntegerField()
    credit = serializers.DecimalField(max_digits=12, decimal_places=2)
    applied_credit = serializers.DecimalField(max_digits=12, decimal_places=2)
    forfeited = serializers.DecimalField(max_digits=12, decimal_places=2)
    net_payable = serializers.DecimalField(max_digits=12, decimal_places=2)
    explanation = serializers.CharField()


def _option(preview):
    return {"product_id": preview.to_product.pk, "product": preview.to_product.name,
            "billing_cycle": preview.billing_cycle, "months": preview.months, "new_price": preview.new_price,
            "term_paid": preview.credit.term_paid, "term_days": preview.credit.term_days,
            "remaining_days": preview.credit.remaining_days, "credit": preview.credit.amount,
            "applied_credit": preview.applied_credit, "forfeited": preview.forfeited,
            "net_payable": preview.net_payable, "explanation": preview.explanation}


class UpgradeInputSerializer(serializers.Serializer):
    product = serializers.IntegerField(min_value=1, help_text="The plan to move to.")


class DomainRenewalInputSerializer(serializers.Serializer):
    years = serializers.IntegerField(min_value=1, max_value=10, default=1)


class TermInputSerializer(serializers.Serializer):
    billing_cycle = serializers.ChoiceField(choices=[(c, BillingCycle(c).label) for c in RECURRING_CYCLES])
    custom_months = serializers.IntegerField(min_value=0, max_value=120, required=False, default=0)
    term_start = serializers.DateTimeField()
    expires_at = serializers.DateTimeField()
    term_paid = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)


class TermOutputSerializer(serializers.Serializer):
    billing_cycle = serializers.CharField()
    custom_months = serializers.IntegerField()
    term_start = serializers.DateTimeField()
    expires_at = serializers.DateTimeField()
    term_paid = serializers.DecimalField(max_digits=12, decimal_places=2)


def _account(request, pk):
    return get_object_or_404(hosting_services.visible_hosting_accounts_for_user(request.user), pk=pk)


class HostingRenewView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: ServiceChangeSerializer}, operation_id="hosting_accounts_renew")
    def post(self, request, pk):
        """Invoice the renewal of a hosting account (returns the existing open one if there is one)."""
        change = services.create_hosting_renewal(request.user, _account(request, pk), request=request)
        return Response(ServiceChangeSerializer(change, context={"request": request}).data)


class HostingUpgradesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=UpgradeOptionSerializer(many=True), operation_id="hosting_accounts_upgrade_options")
    def get(self, request, pk):
        """Every plan the account can move to, with the server-side calculation for each."""
        account = _account(request, pk)
        services.require_upgradeable(account)
        return Response(UpgradeOptionSerializer([_option(p) for p in services.available_upgrades(account)],
                                                many=True).data)

    @extend_schema(request=UpgradeInputSerializer, responses={200: ServiceChangeSerializer},
                   operation_id="hosting_accounts_upgrade")
    def post(self, request, pk):
        """Invoice an upgrade. Only the plan is read from the request; the credit is recalculated here."""
        serializer = UpgradeInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = get_object_or_404(Product, pk=serializer.validated_data["product"])
        change = services.create_upgrade(request.user, _account(request, pk), product, request=request)
        return Response(ServiceChangeSerializer(change, context={"request": request}).data)


class HostingTermView(APIView):
    """Staff (``manage_billing``): record the paid term of an existing account."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=TermInputSerializer, responses=TermOutputSerializer, operation_id="hosting_accounts_term")
    def put(self, request, pk):
        serializer = TermInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        account = services.set_hosting_term(request.user, _account(request, pk), billing_cycle=data["billing_cycle"],
                                            custom_months=data["custom_months"], term_start=data["term_start"],
                                            expires_at=data["expires_at"], term_paid=data["term_paid"],
                                            request=request)
        return Response(TermOutputSerializer(account).data)


class DomainRenewalView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=DomainRenewalInputSerializer, responses={200: ServiceChangeSerializer},
                   operation_id="domains_renewal_invoice")
    def post(self, request, pk):
        """Invoice the renewal of a domain for a number of years."""
        serializer = DomainRenewalInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        domain = get_object_or_404(domain_services.visible_domains_for_user(request.user), pk=pk)
        change = services.create_domain_renewal(request.user, domain, serializer.validated_data["years"],
                                                request=request)
        return Response(ServiceChangeSerializer(change, context={"request": request}).data)


class ServiceChangeFilter(filters.FilterSet):
    status = filters.ChoiceFilter(choices=ChangeStatus.choices)
    kind = filters.ChoiceFilter(choices=ChangeKind.choices)
    client = filters.NumberFilter(field_name="client_id")
    invoice = filters.NumberFilter(field_name="invoice_id")

    class Meta:
        model = ServiceChange
        fields = []


class NoteInputSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=500)


class ServiceChangeViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Renewals and upgrades: staff see all, customers their own clients'. ``retry``/``dismiss`` need ``manage_billing``."""

    permission_classes = [IsAuthenticated]
    serializer_class = ServiceChangeSerializer
    filterset_class = ServiceChangeFilter
    ordering_fields = ["created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ServiceChange.objects.none()
        return services.visible_changes_for_user(self.request.user)

    @extend_schema(request=None, responses=ServiceChangeSerializer)
    @action(detail=True, methods=["post"])
    def retry(self, request, *args, **kwargs):
        change = services.retry_change(request.user, self.get_object(), request=request)
        return Response(ServiceChangeSerializer(change, context={"request": request}).data)

    @extend_schema(request=NoteInputSerializer, responses=ServiceChangeSerializer)
    @action(detail=True, methods=["post"])
    def dismiss(self, request, *args, **kwargs):
        serializer = NoteInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change = services.dismiss_change(request.user, self.get_object(), note=serializer.validated_data["note"],
                                         request=request)
        return Response(ServiceChangeSerializer(change, context={"request": request}).data, status=status.HTTP_200_OK)
