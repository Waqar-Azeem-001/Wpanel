from django.contrib import admin

from .models import HostingAccount


@admin.register(HostingAccount)
class HostingAccountAdmin(admin.ModelAdmin):
    list_display = ("username", "domain", "client", "product", "server", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("username", "domain", "client__company_name", "client__email")
    raw_id_fields = ("client", "product", "server")
    readonly_fields = [f.name for f in HostingAccount._meta.fields]

    # Real changes go through the staff pages (service layer + audit trail).
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
