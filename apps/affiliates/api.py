"""
REST API for affiliates: joining, referral links' outcomes (commissions), payouts and the programme settings.

An affiliate sees only their own record, commissions and payouts, and referred customers only as anonymous labels.
Staff need ``view_affiliates`` to read and ``manage_affiliates`` to act; the settings need ``manage_settings``.
Every rule lives in ``services``.
"""
from datetime import date

from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.roles import perm
from apps.core.exceptions import ServiceError
from apps.core.permissions import AuthenticatedReadPermission, HasPortalPermission

from . import services
from .models import (Affiliate, AffiliateSettings, AffiliateStatus, Commission, CommissionKind, CommissionStatus,
                     Payout)


class AffiliateSerializer(serializers.ModelSerializer):
    status = serializers.ChoiceField(choices=AffiliateStatus.choices, read_only=True)
    commission_kind = serializers.ChoiceField(choices=CommissionKind.choices, allow_blank=True, read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    referral_url = serializers.SerializerMethodField()

    class Meta:
        model = Affiliate
        fields = ["id", "email", "code", "status", "commission_kind", "commission_value", "payout_details",
                  "visit_count", "review_note", "referral_url", "created_at"]
        read_only_fields = fields

    def get_referral_url(self, affiliate) -> str:
        return services.referral_url(affiliate)


class EnrolSerializer(serializers.Serializer):
    accept_terms = serializers.BooleanField()
    payout_details = serializers.CharField(required=False, allow_blank=True, default="", max_length=1000)


class PayoutDetailsSerializer(serializers.Serializer):
    payout_details = serializers.CharField(allow_blank=True, max_length=1000)


class MeSerializer(AffiliateSerializer):
    balances = serializers.SerializerMethodField()
    stats = serializers.SerializerMethodField()

    class Meta(AffiliateSerializer.Meta):
        fields = AffiliateSerializer.Meta.fields + ["balances", "stats"]
        read_only_fields = fields

    def get_balances(self, affiliate) -> dict:
        data = services.balances(affiliate)
        return {k: (str(v) if k != "counts" else v) for k, v in data.items()}

    def get_stats(self, affiliate) -> dict:
        return services.affiliate_stats(affiliate)


class AffiliateMeView(APIView):
    """The signed-in person's own affiliate record: read it, join the programme, or update payout details."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=MeSerializer)
    def get(self, request):
        affiliate = services.get_affiliate(request.user)
        if affiliate is None:
            raise ServiceError("You are not part of the affiliate programme.", code="not_affiliate", status_code=404)
        return Response(MeSerializer(affiliate).data)

    @extend_schema(request=EnrolSerializer, responses={201: MeSerializer})
    def post(self, request):
        serializer = EnrolSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        affiliate = services.enrol(request.user, request=request, **serializer.validated_data)
        return Response(MeSerializer(affiliate).data, status=201)

    @extend_schema(request=PayoutDetailsSerializer, responses=MeSerializer)
    def patch(self, request):
        affiliate = services.get_affiliate(request.user)
        if affiliate is None:
            raise ServiceError("You are not part of the affiliate programme.", code="not_affiliate", status_code=404)
        serializer = PayoutDetailsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.update_payout_details(request.user, affiliate, serializer.validated_data["payout_details"],
                                       request=request)
        return Response(MeSerializer(affiliate).data)


class NoteSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=500)


class RequiredNoteSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=500)


class OverrideSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=CommissionKind.choices, required=False, allow_blank=True, default="")
    value = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True, default=None)


class CodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=32)


class PayoutRecordSerializer(serializers.Serializer):
    method = serializers.CharField(max_length=100)
    reference = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    paid_on = serializers.DateField(required=False, allow_null=True, default=None)
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=500)
    commission_ids = serializers.ListField(child=serializers.IntegerField(), required=False, allow_null=True,
                                           default=None)


class PayoutSerializer(serializers.ModelSerializer):
    code = serializers.CharField(read_only=True)
    affiliate_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Payout
        fields = ["id", "code", "affiliate_id", "amount", "currency", "method", "reference", "paid_on", "note",
                  "created_at"]
        read_only_fields = fields


class AffiliateFilter(filters.FilterSet):
    status = filters.ChoiceFilter(choices=AffiliateStatus.choices)

    class Meta:
        model = Affiliate
        fields = ["status"]


class AffiliateViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Staff: every affiliate, and the actions on one."""

    permission_classes = [HasPortalPermission]
    serializer_class = AffiliateSerializer
    filterset_class = AffiliateFilter
    search_fields = ["code", "user__email", "user__first_name", "user__last_name"]
    ordering_fields = ["created_at", "visit_count"]

    @property
    def required_permissions(self):
        return [perm("view_affiliates")] if self.request.method in ("GET", "HEAD", "OPTIONS") \
            else [perm("manage_affiliates")]

    def get_queryset(self):
        return Affiliate.objects.select_related("user")

    @extend_schema(request=NoteSerializer, responses=AffiliateSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, *args, **kwargs):
        serializer = NoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(AffiliateSerializer(services.approve_affiliate(
            request.user, self.get_object(), note=serializer.validated_data["note"], request=request)).data)

    @extend_schema(request=RequiredNoteSerializer, responses=AffiliateSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, *args, **kwargs):
        serializer = RequiredNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(AffiliateSerializer(services.reject_affiliate(
            request.user, self.get_object(), note=serializer.validated_data["note"], request=request)).data)

    @extend_schema(request=NoteSerializer, responses=AffiliateSerializer)
    @action(detail=True, methods=["post"])
    def suspend(self, request, *args, **kwargs):
        serializer = NoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(AffiliateSerializer(services.suspend_affiliate(
            request.user, self.get_object(), note=serializer.validated_data["note"], request=request)).data)

    @extend_schema(request=NoteSerializer, responses=AffiliateSerializer)
    @action(detail=True, methods=["post"])
    def reactivate(self, request, *args, **kwargs):
        serializer = NoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(AffiliateSerializer(services.reactivate_affiliate(
            request.user, self.get_object(), note=serializer.validated_data["note"], request=request)).data)

    @extend_schema(request=OverrideSerializer, responses=AffiliateSerializer)
    @action(detail=True, methods=["post"])
    def rule(self, request, *args, **kwargs):
        serializer = OverrideSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(AffiliateSerializer(services.set_override(
            request.user, self.get_object(), kind=data["kind"], value=data["value"], request=request)).data)

    @extend_schema(request=CodeSerializer, responses=AffiliateSerializer)
    @action(detail=True, methods=["post"])
    def code(self, request, *args, **kwargs):
        serializer = CodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(AffiliateSerializer(services.set_code(
            request.user, self.get_object(), serializer.validated_data["code"], request=request)).data)

    @extend_schema(request=PayoutRecordSerializer, responses={201: PayoutSerializer})
    @action(detail=True, methods=["post"])
    def payout(self, request, *args, **kwargs):
        serializer = PayoutRecordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        paid_on = data["paid_on"] if isinstance(data["paid_on"], date) else None
        payout = services.record_payout(request.user, self.get_object(), method=data["method"],
                                        reference=data["reference"], paid_on=paid_on, note=data["note"],
                                        commission_ids=data["commission_ids"], request=request)
        return Response(PayoutSerializer(payout).data, status=201)


class CommissionSerializer(serializers.ModelSerializer):
    code = serializers.CharField(read_only=True)
    status = serializers.ChoiceField(choices=CommissionStatus.choices, read_only=True)
    kind = serializers.ChoiceField(choices=CommissionKind.choices, read_only=True)
    affiliate_id = serializers.IntegerField(read_only=True)
    invoice_id = serializers.IntegerField(read_only=True)
    customer = serializers.CharField(source="referral.label", read_only=True)
    payout_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Commission
        fields = ["id", "code", "affiliate_id", "customer", "invoice_id", "status", "currency", "base_amount", "kind",
                  "rate", "original_amount", "amount", "eligible_at", "note", "needs_review", "payout_id",
                  "created_at"]
        read_only_fields = fields

    def to_representation(self, commission):
        data = super().to_representation(commission)
        request = self.context.get("request")
        if not (request and services.can_view(request.user)):
            data.pop("invoice_id", None)  # an affiliate never learns which invoice a customer paid
            data.pop("needs_review", None)
        return data


class CommissionFilter(filters.FilterSet):
    status = filters.ChoiceFilter(choices=CommissionStatus.choices)
    affiliate = filters.NumberFilter(field_name="affiliate_id")

    class Meta:
        model = Commission
        fields = ["status", "affiliate"]


class CommissionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CommissionSerializer
    filterset_class = CommissionFilter
    ordering_fields = ["created_at", "amount"]

    def get_queryset(self):
        return services.visible_commissions(self.request.user)

    @extend_schema(request=None, responses=CommissionSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, *args, **kwargs):
        commission = services.approve_commission(request.user, self.get_object(), request=request)
        return Response(CommissionSerializer(commission, context={"request": request}).data)

    @extend_schema(request=RequiredNoteSerializer, responses=CommissionSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, *args, **kwargs):
        serializer = RequiredNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        commission = services.reject_commission(request.user, self.get_object(),
                                                note=serializer.validated_data["note"], request=request)
        return Response(CommissionSerializer(commission, context={"request": request}).data)


class PayoutViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = PayoutSerializer
    ordering_fields = ["paid_on", "amount"]

    def get_queryset(self):
        return services.visible_payouts(self.request.user)


class AffiliateSettingsSerializer(serializers.ModelSerializer):
    commission_kind = serializers.ChoiceField(choices=CommissionKind.choices)

    class Meta:
        model = AffiliateSettings
        fields = ["enabled", "require_approval", "cookie_days", "commission_kind", "commission_value",
                  "recurring_months", "hold_days", "minimum_payout", "program_terms", "updated_at"]
        read_only_fields = ["updated_at"]


class AffiliateSettingsView(APIView):
    """GET (any signed-in person, so an applicant sees the terms) and PUT/PATCH (``manage_settings``)."""

    permission_classes = [AuthenticatedReadPermission]
    required_permissions = [perm("manage_settings")]

    @extend_schema(responses=AffiliateSettingsSerializer)
    def get(self, request):
        return Response(AffiliateSettingsSerializer(AffiliateSettings.load()).data)

    @extend_schema(request=AffiliateSettingsSerializer, responses=AffiliateSettingsSerializer)
    def put(self, request):
        return self._save(request, partial=False)

    @extend_schema(request=AffiliateSettingsSerializer, responses=AffiliateSettingsSerializer)
    def patch(self, request):
        return self._save(request, partial=True)

    def _save(self, request, *, partial):
        serializer = AffiliateSettingsSerializer(AffiliateSettings.load(), data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        return Response(AffiliateSettingsSerializer(services.save_settings(
            request.user, request=request, **serializer.validated_data)).data)


class ReportSerializer(serializers.Serializer):
    affiliates = serializers.DictField(child=serializers.IntegerField())
    referrals = serializers.IntegerField()
    visits = serializers.IntegerField()
    commissions = serializers.DictField(child=serializers.DictField())
    paid_out = serializers.DecimalField(max_digits=12, decimal_places=2)
    awaiting_review = serializers.IntegerField()
    top = serializers.ListField(child=serializers.DictField())


class ReportView(APIView):
    """Staff (``view_affiliates``): the programme at a glance. ``?days=`` limits commissions to the last 7, 30 or 90."""

    permission_classes = [HasPortalPermission]
    required_permissions = [perm("view_affiliates")]

    @extend_schema(responses=ReportSerializer)
    def get(self, request):
        try:
            days = int(request.query_params.get("days", 0))
        except ValueError:
            days = 0
        return Response(ReportSerializer(services.report(days=days if days in (7, 30, 90) else None)).data)

