from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor_repr", "target_type", "target_id", "ip_address")
    list_filter = ("action", "target_type")
    search_fields = ("action", "actor_repr", "target_repr", "target_id", "request_id")
    date_hierarchy = "created_at"

    # Audit records are append-only.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]
