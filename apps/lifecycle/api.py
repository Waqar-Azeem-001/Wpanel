"""
REST API for cancellations and the service lifecycle.

A customer sees only their own clients' requests and only the owner of an account may ask; staff need ``view_hosting`` /
``view_domains`` to read and ``manage_hosting`` / ``manage_domains`` to act (and ``manage_billing`` to refund). The
lifecycle timings need ``view_settings`` to read and ``manage_settings`` to change. Every rule lives in ``services``.
"""
from django.shortcuts import get_object_or_404
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.roles import perm
from apps.billing.models import Transaction
from apps.core.exceptions import ServiceError
from apps.core.permissions import HasPortalPermission
from apps.domains import services as domain_services
from apps.hosting import services as hosting_services

from . import services
from .models import CancellationReason, CancellationRequest, CancellationStatus, LifecycleSettings, Timing


class CancellationSerializer(serializers.ModelSerializer):
    code = serializers.CharField(read_only=True)
    status = serializers.ChoiceField(choices=CancellationStatus.choices, read_only=True)
    reason_code = serializers.ChoiceField(choices=CancellationReason.choices, read_only=True)
    timing = serializers.ChoiceField(choices=Timing.choices, read_only=True)
    service_type = serializers.CharField(source="kind", read_only=True)
    service_id = serializers.SerializerMethodField()
    service_name = serializers.CharField(read_only=True)
    client_id = serializers.IntegerField(read_only=True)
    requested_by = serializers.SerializerMethodField(help_text="Staff only.")
    reviewed_by = serializers.SerializerMethodField(help_text="Staff only.")

    class Meta:
        model = CancellationRequest
        fields = ["id", "code", "service_type", "service_id", "service_name", "client_id", "status", "reason_code",
                  "reason_text", "timing", "on_behalf", "term_expires_at", "review_note", "effective_at",
                  "refund_amount", "reviewed_at", "completed_at", "created_at", "requested_by", "reviewed_by",
                  "refund_payment", "refund_transaction", "last_error", "refund_error"]
        read_only_fields = fields

    STAFF_ONLY = ("requested_by", "reviewed_by", "refund_payment", "refund_transaction", "last_error", "refund_error")

    def get_service_id(self, cr) -> int:
        return cr.hosting_account_id or cr.domain_id

    def get_requested_by(self, cr) -> str | None:
        return cr.requested_by.email if cr.requested_by_id else None

    def get_reviewed_by(self, cr) -> str | None:
        return cr.reviewed_by.email if cr.reviewed_by_id else None

    def to_representation(self, cr):
        data = super().to_representation(cr)
        request = self.context.get("request")
        if not (request and services.is_staff_reviewer(request.user)):
            for name in self.STAFF_ONLY:  # a customer never sees which staff member handled it, or internal errors
                data.pop(name, None)
        return data


class CancellationCreateSerializer(serializers.Serializer):
    service_type = serializers.ChoiceField(choices=[("hosting", "Hosting"), ("domain", "Domain")])
    service_id = serializers.IntegerField(min_value=1)
    reason_code = serializers.ChoiceField(choices=CancellationReason.choices)
    reason_text = serializers.CharField(required=False, allow_blank=True, default="", max_length=1000)
    timing = serializers.ChoiceField(choices=Timing.choices, default=Timing.END_OF_TERM)


class ApproveSerializer(serializers.Serializer):
    timing = serializers.ChoiceField(choices=Timing.choices, required=False)
    refund_amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False, default=0)
    refund_payment_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=1000)


class RejectSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=1000)


class RefundPaymentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    invoice_number = serializers.CharField()
    method = serializers.CharField()
    currency = serializers.CharField()
    refundable = serializers.DecimalField(max_digits=12, decimal_places=2)


class RefundOptionsSerializer(serializers.Serializer):
    suggested = serializers.DecimalField(max_digits=12, decimal_places=2,
                                         help_text="The unused part of the paid term (excluding tax).")
    payments = RefundPaymentSerializer(many=True)


class CancellationFilter(filters.FilterSet):
    status = filters.ChoiceFilter(choices=CancellationStatus.choices)
    service_type = filters.ChoiceFilter(choices=[("hosting", "Hosting"), ("domain", "Domain")], method="by_type")

    class Meta:
        model = CancellationRequest
        fields = ["status", "service_type"]

    def by_type(self, queryset, name, value):
        return queryset.filter(hosting_account__isnull=False) if value == "hosting" else queryset.filter(
            domain__isnull=False)


class CancellationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                          viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CancellationSerializer
    filterset_class = CancellationFilter
    ordering_fields = ["created_at"]

    def get_queryset(self):
        return services.visible_requests_for_user(self.request.user)

    @extend_schema(request=CancellationCreateSerializer, responses={201: CancellationSerializer})
    def create(self, request, *args, **kwargs):
        serializer = CancellationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        visible = (hosting_services.visible_hosting_accounts_for_user(request.user)
                   if data["service_type"] == "hosting" else domain_services.visible_domains_for_user(request.user))
        service = get_object_or_404(visible, pk=data["service_id"])
        cr = services.request_cancellation(request.user, service, reason_code=data["reason_code"],
                                           reason_text=data["reason_text"], timing=data["timing"], request=request)
        return Response(CancellationSerializer(cr, context={"request": request}).data, status=201)

    @extend_schema(request=None, responses=CancellationSerializer)
    @action(detail=True, methods=["post"])
    def withdraw(self, request, *args, **kwargs):
        cr = services.withdraw(request.user, self.get_object(), request=request)
        return Response(CancellationSerializer(cr, context={"request": request}).data)

    @extend_schema(request=ApproveSerializer, responses=CancellationSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, *args, **kwargs):
        serializer = ApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        payment = None
        if data["refund_payment_id"] is not None:
            payment = Transaction.objects.filter(pk=data["refund_payment_id"]).first()
            if payment is None:  # the same answer whether it does not exist or belongs to someone else
                raise ServiceError("That payment cannot be refunded for this service.",
                                   code="refund_payment_invalid")
        cr = services.approve(request.user, self.get_object(), timing=data.get("timing"),
                              refund_amount=data["refund_amount"], refund_payment=payment, note=data["note"],
                              request=request)
        return Response(CancellationSerializer(cr, context={"request": request}).data)

    @extend_schema(request=RejectSerializer, responses=CancellationSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, *args, **kwargs):
        serializer = RejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cr = services.reject(request.user, self.get_object(), note=serializer.validated_data["note"], request=request)
        return Response(CancellationSerializer(cr, context={"request": request}).data)

    @extend_schema(request=None, responses=CancellationSerializer)
    @action(detail=True, methods=["post"])
    def retry(self, request, *args, **kwargs):
        cr = services.retry(request.user, self.get_object(), request=request)
        return Response(CancellationSerializer(cr, context={"request": request}).data)

    @extend_schema(responses=RefundOptionsSerializer)
    @action(detail=True, methods=["get"], url_path="refund-options", filter_backends=[], pagination_class=None)
    def refund_options(self, request, *args, **kwargs):
        cr = self.get_object()
        if not services.can_manage(request.user, cr.service) or not request.user.has_perm(perm("manage_billing")):
            raise ServiceError("You do not have permission to perform this action.", code="permission_denied",
                               status_code=403)
        options = services.refund_options(cr)
        return Response(RefundOptionsSerializer({
            "suggested": options["suggested"],
            "payments": [{"id": tx.pk, "invoice_number": tx.invoice.number or str(tx.invoice_id),
                          "method": tx.method_name, "currency": tx.currency, "refundable": available}
                         for tx, available in options["payments"]]}).data)


class LifecycleSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = LifecycleSettings
        fields = ["grace_after_days", "suspend_after_days", "terminate_after_days", "auto_suspend", "auto_terminate",
                  "unsuspend_on_payment", "updated_at"]
        read_only_fields = ["updated_at"]


class LifecycleSettingsView(APIView):
    """GET (``view_settings``) and PUT/PATCH (``manage_settings``) the timings of the unpaid-service lifecycle."""

    permission_classes = [HasPortalPermission]

    @property
    def required_permissions(self):
        return [perm("view_settings")] if self.request.method == "GET" else [perm("manage_settings")]

    @extend_schema(responses=LifecycleSettingsSerializer)
    def get(self, request):
        return Response(LifecycleSettingsSerializer(LifecycleSettings.load()).data)

    @extend_schema(request=LifecycleSettingsSerializer, responses=LifecycleSettingsSerializer)
    def put(self, request):
        return self._save(request, partial=False)

    @extend_schema(request=LifecycleSettingsSerializer, responses=LifecycleSettingsSerializer)
    def patch(self, request):
        return self._save(request, partial=True)

    def _save(self, request, *, partial):
        serializer = LifecycleSettingsSerializer(LifecycleSettings.load(), data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        row = services.save_settings(request.user, request=request, **serializer.validated_data)
        return Response(LifecycleSettingsSerializer(row).data)


class LifecycleOverviewSerializer(serializers.Serializer):
    counts = serializers.DictField(child=serializers.IntegerField(), help_text="Services per lifecycle stage.")
    pending_cancellations = serializers.IntegerField()
    scheduled_cancellations = serializers.IntegerField()
    needing_attention = serializers.IntegerField()
    services = serializers.DictField(child=serializers.ListField(child=serializers.DictField()),
                                     help_text="Hosting accounts in each stage that needs action.")


class LifecycleOverviewView(APIView):
    """Staff (``view_hosting`` or ``view_domains``): where every service is in its life."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=LifecycleOverviewSerializer)
    def get(self, request):
        if not services.is_staff_reviewer(request.user):
            raise ServiceError("You do not have permission to perform this action.", code="permission_denied",
                               status_code=403)
        data = services.overview()
        return Response(LifecycleOverviewSerializer({
            "counts": {stage.value: n for stage, n in data["counts"].items()},
            "pending_cancellations": data["pending_cancellations"],
            "scheduled_cancellations": data["scheduled_cancellations"],
            "needing_attention": data["needing_attention"],
            "services": {stage.value: [{"id": a.pk, "domain": a.domain, "client_id": a.client_id,
                                        "expires_at": a.expires_at.isoformat() if a.expires_at else None}
                                       for a in accounts] for stage, accounts in data["rows"].items()}}).data)

