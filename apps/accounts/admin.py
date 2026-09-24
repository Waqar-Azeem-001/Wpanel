from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.core.exceptions import ServiceError

from . import services
from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("-date_joined",)
    list_display = ("email", "full_name", "role", "status", "is_email_verified", "date_joined", "last_login")
    list_filter = ("role", "status", "is_staff")
    search_fields = ("email", "first_name", "last_name", "phone")
    readonly_fields = ("role", "status", "email_verified_at", "is_active", "is_staff", "is_superuser",
                       "date_joined", "last_login", "updated_at")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("first_name", "last_name", "phone")}),
        ("Access", {
            "fields": ("role", "status", "email_verified_at", "is_active", "is_staff", "is_superuser"),
            "description": "Role and status are changed with the actions on the user list so the change is "
                           "authorised and audited.",
        }),
        ("Dates", {"fields": ("date_joined", "last_login", "updated_at")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)
    filter_horizontal = ()
    actions = ["suspend_users", "activate_users"]

    @admin.display(boolean=True, description="Verified")
    def is_email_verified(self, obj):
        return obj.is_email_verified

    def _set_status(self, request, queryset, status):
        for user in queryset:
            try:
                services.set_account_status(request.user, user, status, reason="admin action", request=request)
            except ServiceError as exc:
                self.message_user(request, f"{user}: {exc.message}", messages.ERROR)

    @admin.action(description="Suspend selected users")
    def suspend_users(self, request, queryset):
        self._set_status(request, queryset, "suspended")

    @admin.action(description="Activate selected users")
    def activate_users(self, request, queryset):
        self._set_status(request, queryset, "active")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            services.sync_role_membership(obj)
