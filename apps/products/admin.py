from django.contrib import admin

from .models import Addon, AddonPrice, Product, ProductPrice, Server


class ReadOnlyMixin:
    """Real changes go through the staff pages (service layer + audit trail)."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ProductPriceInline(admin.TabularInline):
    model = ProductPrice
    extra = 0
    readonly_fields = [f.name for f in ProductPrice._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class AddonPriceInline(admin.TabularInline):
    model = AddonPrice
    extra = 0
    readonly_fields = [f.name for f in AddonPrice._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Product)
class ProductAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ("name", "type", "status", "whm_package_name", "created_at")
    list_filter = ("type", "status")
    search_fields = ("name", "slug", "description")
    inlines = [ProductPriceInline]


@admin.register(Addon)
class AddonAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ("name", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "slug", "description")
    inlines = [AddonPriceInline]


@admin.register(Server)
class ServerAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ("name", "hostname", "ip_address", "status", "max_accounts")
    list_filter = ("status",)
    search_fields = ("name", "hostname", "ip_address")
