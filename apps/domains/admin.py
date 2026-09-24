import json

from django import forms
from django.contrib import admin

from apps.audit import services as audit

from .models import Domain, DnsRecord, RegistrarProvider, TldPricing


class RegistrarProviderForm(forms.ModelForm):
    """Mirrors ``notifications.EmailProviderAdmin``: same DB-configured-provider precedent."""

    credentials = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 4}),
        help_text='JSON object, e.g. {"api_key": "..."}. Leave blank to keep the stored value. '
                  'The Manual provider needs none.',
    )

    class Meta:
        model = RegistrarProvider
        fields = ["name", "kind", "sandbox", "credentials", "is_active"]

    def clean_credentials(self):
        raw = self.cleaned_data.get("credentials", "").strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise forms.ValidationError("Enter valid JSON.") from exc
        if not isinstance(data, dict):
            raise forms.ValidationError("Must be a JSON object.")
        return data

    def save(self, commit=True):
        if self.cleaned_data.get("credentials") is not None:
            self.instance.set_credentials(self.cleaned_data["credentials"])
        return super().save(commit)


@admin.register(RegistrarProvider)
class RegistrarProviderAdmin(admin.ModelAdmin):
    form = RegistrarProviderForm
    list_display = ("name", "kind", "sandbox", "is_active", "updated_at")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit.record(
            "registrar_provider.updated" if change else "registrar_provider.created",
            actor=request.user, target=obj,
            metadata={"fields": [f for f in form.changed_data if f != "credentials"],
                      "credentials_changed": "credentials" in form.changed_data},
            request=request,
        )


@admin.register(TldPricing)
class TldPricingAdmin(admin.ModelAdmin):
    list_display = ("tld", "register_price", "renew_price", "transfer_price", "is_active")
    list_filter = ("is_active",)
    search_fields = ("tld",)

    # Real changes go through the staff pages (service layer + audit trail).
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class DnsRecordInline(admin.TabularInline):
    model = DnsRecord
    extra = 0
    readonly_fields = [f.name for f in DnsRecord._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("name", "client", "status", "expires_at", "auto_renew", "is_locked")
    list_filter = ("status", "tld", "auto_renew", "is_locked")
    search_fields = ("name", "client__company_name", "client__email", "provider_ref")
    raw_id_fields = ("client",)
    readonly_fields = [f.name for f in Domain._meta.fields if f.name not in ("id",)]
    inlines = [DnsRecordInline]

    def has_add_permission(self, request):
        return False  # Registration/transfer go through the availability + request flow.

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
