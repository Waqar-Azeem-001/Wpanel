from django import forms
from django.contrib import admin

from apps.audit import services as audit

from .models import EmailMessage, EmailProvider, Notification


class EmailProviderForm(forms.ModelForm):
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep the stored password.",
    )

    class Meta:
        model = EmailProvider
        fields = ["name", "kind", "host", "port", "username", "password", "use_tls", "use_ssl", "timeout",
                  "from_email", "is_active"]

    def save(self, commit=True):
        if self.cleaned_data.get("password"):
            self.instance.set_password(self.cleaned_data["password"])
        return super().save(commit)


@admin.register(EmailProvider)
class EmailProviderAdmin(admin.ModelAdmin):
    form = EmailProviderForm
    list_display = ("name", "kind", "host", "port", "from_email", "is_active", "updated_at")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit.record(
            "email_provider.updated" if change else "email_provider.created",
            actor=request.user,
            target=obj,
            metadata={"fields": [f for f in form.changed_data if f != "password"],
                      "password_changed": "password" in form.changed_data},
            request=request,
        )


@admin.register(EmailMessage)
class EmailMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "to_email", "subject", "event", "status", "attempts", "sent_at", "opened_at")
    list_filter = ("status", "event", "template", "is_sensitive")
    search_fields = ("to_email", "subject")
    exclude = ("sensitive_body",)  # encrypted secret content is never shown, not even as ciphertext
    readonly_fields = [f.name for f in EmailMessage._meta.fields if f.name != "sensitive_body"]

    def has_add_permission(self, request):
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "event", "title", "read_at")
    list_filter = ("event",)
    search_fields = ("user__email", "title")
    raw_id_fields = ("user",)
