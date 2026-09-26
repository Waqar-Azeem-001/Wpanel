"""
Plain forms (not ModelForm): validation only. The service layer applies changes,
authorises them and audits them - see apps/accounts/forms.py for why.
"""
import json

from django import forms

from .models import (CATEGORY_ICONS, KNOWN_RESOURCE_LIMITS, Addon, AddonKind, BillingCycle, CategoryKind, StoreCategory, CatalogStatus, Product, ProductType, Server,
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


LIMIT_LABELS = {
    "disk_mb": "Disk space (MB)", "bandwidth_mb": "Bandwidth (MB)", "email_accounts": "Email accounts", "databases": "Databases",
    "ftp_accounts": "FTP accounts", "subdomains": "Subdomains", "parked_domains": "Parked domains",
    "addon_domains": "Addon domains", "websites": "Websites",
}


class FirstPriceMixin:
    """The price box on the create forms: the first price (billing cycle, price, setup fee) is saved with the new item."""

    cycle_choices = BillingCycle.choices

    def add_price_fields(self):
        self.fields["billing_cycle"] = forms.ChoiceField(choices=self.cycle_choices, required=False, label="Billing cycle",
                                                         initial=BillingCycle.MONTHLY)
        self.fields["custom_months"] = forms.IntegerField(min_value=1, max_value=120, required=False, label="Months",
                                                          help_text="Only for the Custom cycle.")
        self.fields["price"] = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2, required=False, label="Price",
                                                  help_text="Leave blank to add prices later.")
        self.fields["setup_fee"] = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2, required=False, initial=0,
                                                      label="Setup fee")

    def clean_price_fields(self, data):
        if data.get("price") is not None:
            if not data.get("billing_cycle"):
                self.add_error("billing_cycle", "Choose how often this price is charged.")
            elif data["billing_cycle"] == BillingCycle.CUSTOM and not data.get("custom_months"):
                self.add_error("custom_months", "Enter the number of months.")
        return data


class ProductForm(FirstPriceMixin, forms.Form):
    cycle_choices = [c for c in BillingCycle.choices if c[0] != BillingCycle.ONE_TIME]

    name = forms.CharField(max_length=150)
    type = forms.ChoiceField(choices=ProductType.choices)
    category = forms.ModelChoiceField(queryset=StoreCategory.objects.filter(kind=CategoryKind.PLANS), required=False,
                                      empty_label="From the type, automatically",
                                      help_text="Where it is listed on the store. Add categories under Products › Categories.")
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}), required=False,
                                  help_text="One feature per line: each line shows as a tick on the plan card.")
    whm_package_name = forms.CharField(max_length=100, required=False, label="WHM package name")
    auto_setup = forms.BooleanField(required=False, initial=True, label="Auto-provision on payment")
    default_auto_renew = forms.BooleanField(required=False, initial=True, label="Auto-renew by default")
    upsell_product = forms.ModelChoiceField(queryset=Product.objects.all(), required=False, label="Suggest this bigger plan",
                                            help_text="Shown in the cart as an upgrade to the same domain and billing cycle.")
    recommended_addons = forms.ModelMultipleChoiceField(
        queryset=Addon.objects.all(), required=False, widget=forms.CheckboxSelectMultiple, label="Recommended add-ons",
        help_text="Highlighted first in the cart and on the plan page.")

    def __init__(self, *args, existing_limits=None, with_price=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.existing_limits = dict(existing_limits or {})
        for key in KNOWN_RESOURCE_LIMITS:
            self.fields[f"limit_{key}"] = forms.IntegerField(
                min_value=0, required=False, label=LIMIT_LABELS.get(key, key), initial=self.existing_limits.get(key))
        self.with_price = with_price
        if with_price:
            self.add_price_fields()
        self.fields["recommended_addons"].label_from_instance = lambda addon: f"{addon.name} ({addon.price_summary})"

    @property
    def limit_fields(self):
        return [self[f"limit_{key}"] for key in KNOWN_RESOURCE_LIMITS]

    @property
    def price_fields(self):
        return [self[name] for name in ("billing_cycle", "custom_months", "price", "setup_fee")] if self.with_price else []

    def clean(self):
        data = super().clean()
        limits = {k: v for k, v in self.existing_limits.items() if k not in KNOWN_RESOURCE_LIMITS}  # keep keys the form does not show
        for key in KNOWN_RESOURCE_LIMITS:
            value = data.get(f"limit_{key}")
            if value is not None:
                limits[key] = value
        data["resource_limits"] = limits
        return self.clean_price_fields(data) if self.with_price else data


class ProductStatusForm(forms.Form):
    status = forms.ChoiceField(choices=CatalogStatus.choices)


class ProductServersForm(forms.Form):
    servers = forms.ModelMultipleChoiceField(queryset=Server.objects.all(), required=False,
                                             widget=forms.CheckboxSelectMultiple)


class AddonForm(FirstPriceMixin, forms.Form):
    name = forms.CharField(max_length=150)
    kind = forms.ChoiceField(choices=AddonKind.choices, initial=AddonKind.GENERAL, required=False,
                             help_text="An SSL certificate goes with a hosting plan, WHOIS privacy with a domain; the "
                                       "storefront lists them under their own headings.")
    category = forms.ModelChoiceField(queryset=StoreCategory.objects.filter(kind=CategoryKind.ADDONS), required=False,
                                      empty_label="From the kind, automatically",
                                      help_text="Only used to list it on the store as a product of its own (SSL, WHOIS privacy).")
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), required=False,
                                  help_text="Shown to the customer. One point per line.")

    def __init__(self, *args, with_price=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.with_price = with_price
        if with_price:
            self.add_price_fields()

    @property
    def price_fields(self):
        return [self[name] for name in ("billing_cycle", "custom_months", "price", "setup_fee")] if self.with_price else []

    def clean(self):
        data = super().clean()
        return self.clean_price_fields(data) if self.with_price else data


class CategoryForm(forms.Form):
    name = forms.CharField(max_length=100)
    short_name = forms.CharField(max_length=40, required=False, help_text="A shorter label for the category bar. Blank = the name.")
    blurb = forms.CharField(max_length=200, required=False, help_text="One line shown under the name.")
    group = forms.CharField(max_length=60, initial="Hosting", help_text="The heading it sits under, e.g. Hosting, Servers, Security and domains.")
    kind = forms.ChoiceField(choices=CategoryKind.choices, initial=CategoryKind.PLANS,
                             help_text="Plans list products; add-ons list SSL, WHOIS privacy and the like; domains leads to the domain search.")
    icon = forms.ChoiceField(choices=CATEGORY_ICONS, initial="bi-box-seam")
    sort_order = forms.IntegerField(min_value=0, max_value=32000, initial=100, help_text="Lower numbers come first.")
    is_active = forms.BooleanField(required=False, initial=True, label="Show on the store")


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
