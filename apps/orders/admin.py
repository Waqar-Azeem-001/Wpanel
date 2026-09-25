from django.contrib import admin

from .models import Cart, CartItem, CouponRedemption, Order, OrderItem


class ReadOnlyMixin:
    """Real changes go through the service layer (with an audit trail)."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class OrderItemInline(ReadOnlyMixin, admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ("kind", "description", "unit_price", "setup_fee", "line_total")
    readonly_fields = fields


@admin.register(Order)
class OrderAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ("reference", "client", "status", "total", "currency", "payment_method_name", "created_at")
    list_filter = ("status",)
    search_fields = ("client__company_name", "client__email", "billing_email", "coupon_code")
    inlines = [OrderItemInline]


class CartItemInline(ReadOnlyMixin, admin.TabularInline):
    model = CartItem
    extra = 0
    fields = ("kind", "product", "addon", "domain_name", "billing_cycle", "years")
    readonly_fields = fields


@admin.register(Cart)
class CartAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ("id", "user", "client", "status", "updated_at")
    list_filter = ("status",)
    inlines = [CartItemInline]


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ("coupon", "order", "client", "discount_amount", "created_at")
