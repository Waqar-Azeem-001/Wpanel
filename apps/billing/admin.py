from django.contrib import admin

from .models import Coupon, PaymentMethod, TaxRule


class ReadOnlyMixin:
    """Real changes go through the staff pages (service layer + audit trail)."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentMethod)
class PaymentMethodAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "sort_order")


@admin.register(TaxRule)
class TaxRuleAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ("name", "country", "rate", "is_active")


@admin.register(Coupon)
class CouponAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ("code", "discount_type", "value", "valid_until", "is_active")
    search_fields = ("code", "description")
