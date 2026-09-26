"""
Plain forms (not ModelForm): validation only. The service layer applies changes,
authorises them and audits them - see apps/accounts/forms.py for why.
"""
import json

from django import forms

from .models import (Addon, BillingCycle, CatalogStatus, Product, ProductType, Server,
                     ServerStatus)


def _fields(model, names):
    return forms.fields_for_model(model, fields=names)


class ResourceLimitsField(forms.CharField):
    """A friendly textarea for the JSON resource-limits field."""

    widget = forms.Textarea(attrs={"rows": 4, "placeholder": '{"disk_mb": 5000, "bandwidth_mb": null}'})

    def to_python(self, value):
        value = (value or "").strip()
        if not value:
            return {}
        try:
            data = json.loads(value)
        except ValueError as exc:
            raise forms.ValidationError("Enter valid JSON, e.g. {\"disk_mb\": 5000}.") from exc
        if not isinstance(data, dict):
            raise forms.ValidationError("Must be a JSON object.")
        return data

    def prepare_value(self, value):
        if isinstance(value, dict):
            return json.dumps(value, indent=2)
        return value


class ProductForm(forms.Form):
    name = forms.CharField(max_length=150)
    type = forms.ChoiceField(choices=ProductType.choices)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    resource_limits = ResourceLimitsField(required=False, label="Resource limits (JSON)")
    whm_package_name = forms.CharField(max_length=100, required=False, label="WHM package name")
    auto_setup = forms.BooleanField(required=False, initial=True, label="Auto-provision on payment")
    default_auto_renew = forms.BooleanField(required=False, initial=True, label="Auto-renew by default")
    upsell_product = forms.ModelChoiceField(queryset=Product.objects.all(), required=False, label="Suggest this bigger plan",
                                            help_text="Shown in the cart as an upgrade to the same domain and billing cycle.")
    recommended_addons = forms.ModelMultipleChoiceField(
        queryset=Addon.objects.all(), required=False, widget=forms.CheckboxSelectMultiple, label="Recommended add-ons",
        help_text="Highlighted first in the cart and on the plan page.")


class ProductStatusForm(forms.Form):
    status = forms.ChoiceField(choices=CatalogStatus.choices)


class ProductServersForm(forms.Form):
    servers = forms.ModelMultipleChoiceField(queryset=Server.objects.all(), required=False,
                                             widget=forms.CheckboxSelectMultiple)


class AddonForm(forms.Form):
    name = forms.CharField(max_length=150)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)


class AddonStatusForm(ProductStatusForm):
    pass


class PriceForm(forms.Form):
    billing_cycle = forms.ChoiceField(choices=BillingCycle.choices)
    custom_months = forms.IntegerField(min_value=1, max_value=120, required=False,
                                       help_text="Only used when billing cycle is Custom.")
    price = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2)
    setup_fee = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2, required=False, initial=0)

    def clean(self):
        data = super().clean()
        # An empty field parses to None (present in cleaned_data), not absent, so
        # setdefault() wouldn't catch it - coerce explicitly with `or 0`.
        data["custom_months"] = data.get("custom_months") or 0
        data["setup_fee"] = data.get("setup_fee") or 0
        return data


class AddonPriceForm(PriceForm):
    billing_cycle = forms.ChoiceField(choices=BillingCycle.choices)  # includes ONE_TIME, valid for addons


class ServerForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.update(_fields(Server, (
            "name", "hostname", "ip_address", "max_accounts", "notes",
            "kind", "api_port", "api_username", "use_ssl", "verify_ssl",
        )))
        self.fields["notes"].widget = forms.Textarea(attrs={"rows": 3})
        # Optional with a model-level default, so a minimal submission (just name/hostname,
        # as before Phase 05) still works instead of failing validation on a missing choice.
        self.fields["kind"].required = False
        self.fields["api_port"].required = False
        self.fields["api_username"].required = False
        self.fields["api_token"] = forms.CharField(
            required=False, widget=forms.PasswordInput(render_value=False),
            help_text="WHM API token. Leave blank to keep the stored value. Not used by the Manual kind.",
        )

    def clean_kind(self):
        from .models import ServerKind

        return self.cleaned_data.get("kind") or ServerKind.MANUAL

    def clean_api_port(self):
        return self.cleaned_data.get("api_port") or 2087


class ServerStatusForm(forms.Form):
    status = forms.ChoiceField(choices=ServerStatus.choices)


class CatalogFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search")
    status = forms.ChoiceField(required=False, choices=[("", "All statuses"), *CatalogStatus.choices])


class ProductFilterForm(CatalogFilterForm):
    type = forms.ChoiceField(required=False, choices=[("", "All types"), *ProductType.choices])
