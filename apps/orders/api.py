from django.shortcuts import get_object_or_404
from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.roles import perm
from apps.products.models import Addon, BillingCycle, Product

from apps.core.exceptions import ServiceError

from . import lifecycle, pricing, services, staff_actions
from .models import CartItem, FulfilmentStatus, ItemKind, Order, OrderItem, OrderStatus


# --- Cart output (computed from the catalogue; a cart stores no prices) ----------------------

class CartItemOutSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=ItemKind.choices)
    description = serializers.CharField()
    parent_id = serializers.IntegerField(allow_null=True)
    domain = serializers.CharField()
    billing_cycle = serializers.CharField()
    custom_months = serializers.IntegerField()
    years = serializers.IntegerField()
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    setup_fee = serializers.DecimalField(max_digits=10, decimal_places=2)
    total = serializers.DecimalField(max_digits=10, decimal_places=2)
    error = serializers.CharField(allow_blank=True)


class CouponOutSerializer(serializers.Serializer):
    code = serializers.CharField()
    discount = serializers.DecimalField(max_digits=10, decimal_places=2)


class TaxOutSerializer(serializers.Serializer):
    name = serializers.CharField()
    rate = serializers.DecimalField(max_digits=5, decimal_places=2)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)


class CartOutSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    client_id = serializers.IntegerField()
    currency = serializers.CharField()
    items = CartItemOutSerializer(many=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2)
    coupon = CouponOutSerializer(allow_null=True)
    coupon_error = serializers.CharField(allow_blank=True)
    discount_total = serializers.DecimalField(max_digits=12, decimal_places=2)
    tax = TaxOutSerializer(allow_null=True)
    total = serializers.DecimalField(max_digits=12, decimal_places=2)
    is_valid = serializers.BooleanField()


def cart_payload(cart, priced=None):
    priced = priced or pricing.price_cart(cart)
    return CartOutSerializer({
        "id": cart.pk, "client_id": cart.client_id, "currency": priced.currency,
        "items": [{
            "id": line.item.pk, "kind": line.item.kind, "description": line.description,
            "parent_id": line.item.parent_id, "domain": line.item.domain_name,
            "billing_cycle": line.item.billing_cycle, "custom_months": line.item.custom_months,
            "years": line.item.years, "unit_price": line.unit_price, "setup_fee": line.setup_fee,
            "total": line.total, "error": line.error,
        } for line in priced.lines],
        "subtotal": priced.subtotal,
        "coupon": {"code": priced.coupon.code, "discount": priced.discount_total} if priced.coupon else None,
        "coupon_error": priced.coupon_error, "discount_total": priced.discount_total,
        "tax": {"name": priced.tax_rule.name, "rate": priced.tax_rate, "amount": priced.tax_total}
        if priced.tax_rule else None,
        "total": priced.total, "is_valid": priced.is_valid,
    }).data


# --- Cart input ------------------------------------------------------------------------------

CLIENT_ID_PARAM = OpenApiParameter("client_id", int, required=False,
                                   description="Required only if you belong to more than one client.")


class ClientScopedSerializer(serializers.Serializer):
    client_id = serializers.IntegerField(required=False)


class CartItemInSerializer(ClientScopedSerializer):
    """What a customer may choose. There is deliberately no price/total field - prices are never accepted."""

    kind = serializers.ChoiceField(choices=ItemKind.choices)
    product_id = serializers.PrimaryKeyRelatedField(source="product", queryset=Product.objects.all(),
                                                    required=False)
    addon_id = serializers.PrimaryKeyRelatedField(source="addon", queryset=Addon.objects.all(), required=False)
    parent_item_id = serializers.IntegerField(required=False)
    domain = serializers.CharField(required=False, allow_blank=True)
    billing_cycle = serializers.ChoiceField(choices=BillingCycle.choices, required=False)
    custom_months = serializers.IntegerField(min_value=0, required=False, default=0)
    years = serializers.IntegerField(min_value=1, required=False, default=1)
    auth_code = serializers.CharField(required=False, write_only=True, trim_whitespace=False)

    REQUIRED = {
        ItemKind.HOSTING: ("product", "domain", "billing_cycle"),
        ItemKind.ADDON: ("addon", "parent_item_id"),
        ItemKind.DOMAIN_REGISTER: ("domain",),
        ItemKind.DOMAIN_TRANSFER: ("domain", "auth_code"),
    }

    def validate(self, attrs):
        missing = {f: ["This field is required for this item type."]
                   for f in self.REQUIRED[attrs["kind"]] if not attrs.get(f)}
        if missing:
            raise serializers.ValidationError(missing)
        return attrs


class CouponInSerializer(ClientScopedSerializer):
    code = serializers.CharField()


class CheckoutInSerializer(ClientScopedSerializer):
    payment_method = serializers.CharField(help_text="Code of an active payment method.")
    notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)


def _visible_cart_items(user):
    queryset = CartItem.objects.select_related("cart", "cart__client", "product", "addon", "parent")
    return queryset if user.has_perm(perm("manage_orders")) else queryset.filter(cart__user=user)


def _open_cart(request, client_id):
    client = services.resolve_client(request.user, client_id)
    return services.get_open_cart(request.user, client)


class CartView(APIView):
    """The signed-in user's open cart, priced from the catalogue."""

    permission_classes = [IsAuthenticated]

    @extend_schema(parameters=[CLIENT_ID_PARAM], responses=CartOutSerializer)
    def get(self, request, *args, **kwargs):
        params = ClientScopedSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        return Response(cart_payload(_open_cart(request, params.validated_data.get("client_id"))))


class CartItemsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=CartItemInSerializer, responses={201: CartOutSerializer})
    def post(self, request, *args, **kwargs):
        serializer = CartItemInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        kind = data["kind"]
        if kind == ItemKind.ADDON:
            parent = get_object_or_404(_visible_cart_items(request.user), pk=data["parent_item_id"])
            services.add_addon(request.user, parent, data["addon"], data.get("billing_cycle"),
                               data.get("custom_months", 0))
            cart = parent.cart
        else:
            client = services.resolve_client(request.user, data.get("client_id"))
            if kind == ItemKind.HOSTING:
                services.add_hosting(request.user, client, data["product"], data["domain"], data["billing_cycle"],
                                     data.get("custom_months", 0))
            elif kind == ItemKind.DOMAIN_REGISTER:
                services.add_domain_registration(request.user, client, data["domain"], data.get("years", 1))
            else:
                services.add_domain_transfer(request.user, client, data["domain"], data["auth_code"])
            cart = services.get_open_cart(request.user, client)
        return Response(cart_payload(cart), status=status.HTTP_201_CREATED)


class CartItemDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={204: None})
    def delete(self, request, pk, *args, **kwargs):
        item = get_object_or_404(_visible_cart_items(request.user), pk=pk)
        services.remove_item(request.user, item)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CartCouponView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=CouponInSerializer, responses=CartOutSerializer)
    def post(self, request, *args, **kwargs):
        serializer = CouponInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cart = _open_cart(request, serializer.validated_data.get("client_id"))
        services.apply_coupon(request.user, cart, serializer.validated_data["code"])
        return Response(cart_payload(cart))

    @extend_schema(parameters=[CLIENT_ID_PARAM], responses=CartOutSerializer)
    def delete(self, request, *args, **kwargs):
        params = ClientScopedSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        cart = _open_cart(request, params.validated_data.get("client_id"))
        services.remove_coupon(request.user, cart)
        return Response(cart_payload(cart))


# --- Orders -----------------------------------------------------------------------------------

class OrderItemSerializer(serializers.ModelSerializer):
    domain = serializers.CharField(source="domain_name", read_only=True)
    fulfilment_status = serializers.ChoiceField(choices=FulfilmentStatus.choices, read_only=True)
    hosting_account_id = serializers.IntegerField(read_only=True)
    domain_id = serializers.IntegerField(read_only=True)
    fulfilment_error = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = ["id", "kind", "description", "parent", "domain", "billing_cycle", "custom_months", "years",
                  "unit_price", "setup_fee", "line_total", "fulfilment_status", "fulfilment_error",
                  "hosting_account_id", "domain_id"]
        read_only_fields = fields  # the transfer auth code is never exposed

    def get_fulfilment_error(self, item) -> str:
        # Why a line failed is an operational detail: staff see it, customers do not.
        request = self.context.get("request")
        return item.fulfilment_error if request and request.user.has_perm(perm("view_orders")) else ""


class OrderSerializer(serializers.ModelSerializer):
    reference = serializers.CharField(read_only=True)
    client_name = serializers.CharField(source="client.display_name", read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    status = serializers.ChoiceField(choices=OrderStatus.choices, read_only=True)
    status_reason = serializers.SerializerMethodField()

    def get_status_reason(self, order) -> str:
        request = self.context.get("request")
        return order.status_reason if request and request.user.has_perm(perm("view_orders")) else ""

    class Meta:
        model = Order
        fields = ["id", "reference", "client", "client_name", "status", "status_reason", "currency", "subtotal",
                  "discount_total", "tax_name", "tax_rate", "tax_total", "total", "coupon_code",
                  "payment_method_name", "notes", "cancel_reason", "billing_name", "billing_company", "billing_email", "billing_country",
                  "billing_tax_id", "items", "created_at", "updated_at"]
        read_only_fields = fields


class CheckoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=CheckoutInSerializer, responses={201: OrderSerializer})
    def post(self, request, *args, **kwargs):
        serializer = CheckoutInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        cart = _open_cart(request, data.get("client_id"))
        order = services.checkout(request.user, cart, payment_method_code=data["payment_method"],
                                  notes=data.get("notes", ""), request=request)
        return Response(OrderSerializer(order, context={"request": request}).data,
                        status=status.HTTP_201_CREATED)


class CancelOrderSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class OrderFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    group = filters.ChoiceFilter(choices=[(k, k) for k in lifecycle.GROUPS], method="filter_group",
                                 help_text="pending, active, fraud or cancelled (the staff screens).")

    class Meta:
        model = Order
        fields = ["status", "client"]

    def filter_group(self, queryset, name, value):
        return queryset.filter(status__in=lifecycle.GROUPS[value])

    def filter_search(self, queryset, name, value):
        return services.search_orders(queryset, value)


class OrderReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="", max_length=500)


class TimelineEventSerializer(serializers.Serializer):
    at = serializers.DateTimeField(source="created_at")
    action = serializers.CharField()
    actor = serializers.CharField(source="actor_repr")
    from_status = serializers.SerializerMethodField()
    to_status = serializers.SerializerMethodField()
    reason = serializers.SerializerMethodField()

    def get_from_status(self, event) -> str:
        return event.metadata.get("from", "")

    def get_to_status(self, event) -> str:
        return event.metadata.get("to", "")

    def get_reason(self, event) -> str:
        return event.metadata.get("reason", "")


class OrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    Staff (``view_orders``) see every order; everyone else sees the orders of
    clients they are a contact of. Cancelling is authorised in the service layer.
    """

    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]
    filterset_class = OrderFilter
    ordering_fields = ["created_at", "total"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Order.objects.none()
        return services.visible_orders_for_user(self.request.user).prefetch_related("items")

    @extend_schema(request=CancelOrderSerializer, responses=OrderSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, *args, **kwargs):
        serializer = CancelOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = services.cancel_order(request.user, self.get_object(),
                                      reason=serializer.validated_data.get("reason", ""), request=request)
        return Response(OrderSerializer(order, context={"request": request}).data)

    def _run(self, request, function, *, with_reason=False):
        order = self.get_object()
        kwargs = {"request": request}
        if with_reason:
            serializer = OrderReasonSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            kwargs["reason"] = serializer.validated_data["reason"]
        return Response(OrderSerializer(function(request.user, order, **kwargs), context={"request": request}).data)

    @extend_schema(request=OrderReasonSerializer, responses=OrderSerializer)
    @action(detail=True, methods=["post"])
    def fraud(self, request, *args, **kwargs):
        """Hold the order as suspected fraud (``manage_orders``). Nothing is fulfilled while it is held."""
        return self._run(request, staff_actions.mark_fraud, with_reason=True)

    @extend_schema(request=OrderReasonSerializer, responses=OrderSerializer)
    @action(detail=True, methods=["post"], url_path="clear-fraud")
    def clear_fraud(self, request, *args, **kwargs):
        """Release a fraud hold (``manage_orders``); a paid order then resumes and is fulfilled."""
        return self._run(request, staff_actions.clear_fraud, with_reason=True)

    @extend_schema(request=None, responses=OrderSerializer)
    @action(detail=True, methods=["post"], url_path="retry-fulfilment")
    def retry_fulfilment(self, request, *args, **kwargs):
        """Retry a failed fulfilment (``manage_orders``); only what is still outstanding is redone."""
        return self._run(request, staff_actions.retry_fulfilment)

    @extend_schema(request=OrderReasonSerializer, responses=OrderSerializer)
    @action(detail=True, methods=["post"])
    def suspend(self, request, *args, **kwargs):
        """Suspend an active order and its hosting accounts (``manage_orders``)."""
        return self._run(request, staff_actions.suspend_order, with_reason=True)

    @extend_schema(request=None, responses=OrderSerializer)
    @action(detail=True, methods=["post"])
    def unsuspend(self, request, *args, **kwargs):
        """Reactivate a suspended order (``manage_orders``)."""
        return self._run(request, staff_actions.unsuspend_order)

    @extend_schema(request=OrderReasonSerializer, responses=OrderSerializer)
    @action(detail=True, methods=["post"])
    def terminate(self, request, *args, **kwargs):
        """Terminate an order and its hosting accounts (``manage_orders``). Final."""
        return self._run(request, staff_actions.terminate_order, with_reason=True)

    @extend_schema(responses=TimelineEventSerializer(many=True))
    @action(detail=True, methods=["get"], filter_backends=[], pagination_class=None)
    def timeline(self, request, *args, **kwargs):
        """The order's audit history, oldest first (staff only: it carries internal detail)."""
        order = self.get_object()  # a stranger's order is a 404 before anything else
        if not request.user.has_perm(perm("view_orders")):
            raise ServiceError("You do not have permission to perform this action.", code="permission_denied",
                               status_code=status.HTTP_403_FORBIDDEN)
        return Response(TimelineEventSerializer(lifecycle.timeline(order), many=True).data)
