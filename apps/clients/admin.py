from django.contrib import admin

from .models import Client, ClientContact


class ClientContactInline(admin.TabularInline):
    model = ClientContact
    extra = 0
    raw_id_fields = ("user",)
    readonly_fields = ("user", "role", "created_at")

    def has_add_permission(self, request, obj=None):
        return False  # Use the staff client pages so changes are authorised and audited.

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("reference", "display_name", "email", "country", "status", "created_at")
    list_filter = ("status", "country", "currency")
    search_fields = ("company_name", "first_name", "last_name", "email", "phone")
    inlines = [ClientContactInline]

    def has_change_permission(self, request, obj=None):
        return False  # Read-only here; edit via /staff/clients/ (service layer + audit).

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
