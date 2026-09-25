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


# --- Phase 07 ------------------------------------------------------------------------------------

import json  # noqa: E402

from django import forms  # noqa: E402
from django.conf import settings  # noqa: E402

from apps.audit import services as audit  # noqa: E402

from .models import (BillableItem, BillingSettings, Invoice, PaymentProvider, Quote, Transaction,  # noqa: E402
                     WebhookEvent)


class PaymentProviderForm(forms.ModelForm):
    """Same DB-configured-provider pattern as the email and registrar providers: secrets are write-only."""

    credentials = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 4}),
        help_text='JSON object, e.g. {"api_key": "..."}. Leave blank to keep the stored value. The test gateway needs none.')
    webhook_secret = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        help_text="The signing secret the gateway uses for its webhooks. Leave blank to keep the stored value.")

    class Meta:
        model = PaymentProvider
        fields = ["name", "kind", "sandbox", "credentials", "webhook_secret", "is_active"]

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
        if self.cleaned_data.get("webhook_secret"):
            self.instance.set_webhook_secret(self.cleaned_data["webhook_secret"])
        return super().save(commit)


@admin.register(PaymentProvider)
class PaymentProviderAdmin(admin.ModelAdmin):
    form = PaymentProviderForm
    list_display = ("name", "kind", "sandbox", "is_active", "webhook_url", "updated_at")
    readonly_fields = ("webhook_url",)

    @admin.display(description="Webhook URL")
    def webhook_url(self, obj):
        if not obj.pk:
            return "Save the provider first."
        from django.urls import reverse

        return settings.SITE_URL.rstrip("/") + reverse("v1:payment-webhook", args=[obj.pk])

    def get_fields(self, request, obj=None):
        return [*super().get_fields(request, obj)]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        secrets = {"credentials", "webhook_secret"}
        audit.record(
            "payment_provider.updated" if change else "payment_provider.created", actor=request.user, target=obj,
            metadata={"fields": [f for f in form.changed_data if f not in secrets],
                      "credentials_changed": "credentials" in form.changed_data,
                      "webhook_secret_changed": "webhook_secret" in form.changed_data},
            request=request)


class _ReadOnly(ReadOnlyMixin, admin.ModelAdmin):
    pass


@admin.register(Invoice)
class InvoiceAdmin(_ReadOnly):
    list_display = ("number", "client", "status", "total", "amount_paid", "due_date")
    list_filter = ("status",)
    search_fields = ("number", "client__company_name", "client__email")


@admin.register(Transaction)
class TransactionAdmin(_ReadOnly):
    list_display = ("id", "invoice", "type", "status", "amount", "currency", "provider", "occurred_at")
    list_filter = ("type", "status")


@admin.register(Quote)
class QuoteAdmin(_ReadOnly):
    list_display = ("number", "client", "status", "total", "valid_until")


@admin.register(BillableItem)
class BillableItemAdmin(_ReadOnly):
    list_display = ("description", "client", "unit_price", "invoice")


@admin.register(WebhookEvent)
class WebhookEventAdmin(_ReadOnly):
    list_display = ("provider", "event_id", "event_type", "status", "created_at")
    list_filter = ("status", "provider")


@admin.register(BillingSettings)
class BillingSettingsAdmin(_ReadOnly):
    list_display = ("company_name", "invoice_prefix", "payment_terms_days")
