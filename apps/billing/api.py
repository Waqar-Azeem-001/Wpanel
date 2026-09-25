from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.roles import perm
from apps.core.permissions import AuthenticatedReadPermission, HasPortalPermission

from . import services
from .models import Coupon, DiscountType, PaymentMethod, TaxRule


class SetActiveSerializer(serializers.Serializer):
    is_active = serializers.BooleanField()


# --- Payment methods ------------------------------------------------------------------------

class PaymentMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMethod
        fields = ["id", "code", "name", "instructions", "is_active", "sort_order"]
        read_only_fields = ["id", "is_active"]


class PaymentMethodViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                           mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Signed-in users see the active methods (for checkout); ``manage_billing`` changes them."""

    permission_classes = [AuthenticatedReadPermission]
    required_permissions = {"*": [perm("manage_billing")]}
    serializer_class = PaymentMethodSerializer
    lookup_field = "code"
    pagination_class = None

    def get_queryset(self):
        return services.visible_payment_methods_for_user(self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = PaymentMethodSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        method = services.save_payment_method(request.user, data.pop("code"), request=request, **data)
        return Response(PaymentMethodSerializer(method).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        instance, data = serializer.instance, serializer.validated_data
        services.save_payment_method(
            self.request.user, instance.code, name=data.get("name", instance.name),
            instructions=data.get("instructions", instance.instructions),
            sort_order=data.get("sort_order", instance.sort_order), request=self.request,
        )

    @extend_schema(request=SetActiveSerializer, responses=PaymentMethodSerializer)
    @action(detail=True, methods=["post"], url_path="status")
    def set_status(self, request, *args, **kwargs):
        serializer = SetActiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        method = services.set_payment_method_active(request.user, self.get_object(),
                                                    serializer.validated_data["is_active"], request=request)
        return Response(PaymentMethodSerializer(method).data)


# --- Tax rules ------------------------------------------------------------------------------

class TaxRuleSerializer(serializers.ModelSerializer):
    country = serializers.CharField(max_length=2, required=False, allow_blank=True, default="")

    class Meta:
        model = TaxRule
        fields = ["id", "name", "country", "rate", "is_active"]
        read_only_fields = ["id", "is_active"]


class TaxRuleViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                     mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [HasPortalPermission]
    serializer_class = TaxRuleSerializer
    queryset = TaxRule.objects.all()
    pagination_class = None

    @property
    def required_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [perm("view_billing")]
        return [perm("manage_billing")]

    def create(self, request, *args, **kwargs):
        serializer = TaxRuleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        rule = services.save_tax_rule(request.user, data.get("country", ""), name=data["name"], rate=data["rate"],
                                      request=request)
        return Response(TaxRuleSerializer(rule).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        instance, data = serializer.instance, serializer.validated_data
        services.save_tax_rule(self.request.user, instance.country, name=data.get("name", instance.name),
                               rate=data.get("rate", instance.rate), request=self.request)

    @extend_schema(request=SetActiveSerializer, responses=TaxRuleSerializer)
    @action(detail=True, methods=["post"], url_path="status")
    def set_status(self, request, *args, **kwargs):
        serializer = SetActiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rule = services.set_tax_rule_active(request.user, self.get_object(), serializer.validated_data["is_active"],
                                            request=request)
        return Response(TaxRuleSerializer(rule).data)


# --- Coupons --------------------------------------------------------------------------------

class CouponSerializer(serializers.ModelSerializer):
    discount_type = serializers.ChoiceField(choices=DiscountType.choices)

    class Meta:
        model = Coupon
        fields = ["id", "code", "description", "discount_type", "value", "valid_from", "valid_until",
                  "max_redemptions", "one_per_client", "min_subtotal", "is_active"]
        read_only_fields = ["id", "is_active"]
        extra_kwargs = {"code": {"validators": []}}  # create is an upsert by code


class CouponViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                    mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Staff only - coupon codes are never listed to customers."""

    permission_classes = [HasPortalPermission]
    serializer_class = CouponSerializer
    queryset = Coupon.objects.all()
    lookup_field = "code"
    lookup_value_regex = r"[^/]+"

    @property
    def required_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [perm("view_billing")]
        return [perm("manage_billing")]

    def get_object(self):
        # Case-insensitive lookup, matching how codes are stored.
        return get_object_or_404(self.get_queryset(), code=self.kwargs["code"].upper())

    def create(self, request, *args, **kwargs):
        serializer = CouponSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        coupon = services.save_coupon(request.user, data.pop("code"), data, request=request)
        return Response(CouponSerializer(coupon).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        services.save_coupon(self.request.user, serializer.instance.code, dict(serializer.validated_data),
                             request=self.request)

    @extend_schema(request=SetActiveSerializer, responses=CouponSerializer)
    @action(detail=True, methods=["post"], url_path="status")
    def set_status(self, request, *args, **kwargs):
        serializer = SetActiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        coupon = services.set_coupon_active(request.user, self.get_object(), serializer.validated_data["is_active"],
                                            request=request)
        return Response(CouponSerializer(coupon).data)
