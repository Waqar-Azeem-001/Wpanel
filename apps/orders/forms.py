"""
Plain forms (not ModelForm): validation only. Note what is *absent*: no form
here has a price, total, discount, tax or expiry field - a browser can only
name what it wants (a product, a cycle, a domain), never what it costs.
"""
from django import forms

from apps.billing.models import PaymentMethod
from apps.core.countries import COUNTRIES
from apps.products.models import STANDARD_CYCLE_MONTHS, BillingCycle, CatalogStatus, Product, decode_option

from .models import OrderStatus


class OptionField(forms.CharField):
    """A billing-cycle choice ('annual', 'custom:4') decoded to a (cycle, months) tuple."""

    def to_python(self, value):
        value = super().to_python(value)
        if not value:
            return None
        try:
            return decode_option(value)
        except ValueError:
            raise forms.ValidationError("Choose a valid billing cycle.")


class AddHostingForm(forms.Form):
    product = forms.IntegerField(widget=forms.HiddenInput)
    cycle = OptionField()
    domain = forms.CharField(label="Domain name", required=False, widget=forms.TextInput(attrs={"placeholder": "example.com"}))
    existing_domain = forms.CharField(required=False)  # a signed-in customer picking one of their own domains

    def clean(self):
        data = super().clean()
        data["domain"] = (data.get("existing_domain") or data.get("domain") or "").strip()
        if not data["domain"]:
            self.add_error("domain", "Enter the domain name this plan is for.")
        return data


class AddDomainForm(forms.Form):
    domain = forms.CharField(widget=forms.HiddenInput)
    years = forms.IntegerField(min_value=1, initial=1)


class AddTransferForm(forms.Form):
    domain = forms.CharField(label="Domain to transfer", widget=forms.TextInput(attrs={"placeholder": "example.com"}))
    auth_code = forms.CharField(label="Authorization (EPP) code")


class AddAddonForm(forms.Form):
    parent_item = forms.IntegerField(widget=forms.HiddenInput)
    addon = forms.IntegerField(widget=forms.HiddenInput)
    option = OptionField(required=False)


class PeriodForm(forms.Form):
    option = OptionField()


class CouponCodeForm(forms.Form):
    code = forms.CharField(max_length=50, label="Coupon code")


OWNER_FIELDS = (("address_line1", "Street address"), ("city", "City"), ("state", "State or province"), ("postcode", "Postcode"))
OWNER_NAMES = [name for name, _ in OWNER_FIELDS]


class CheckoutForm(forms.Form):
    payment_method = forms.ChoiceField(widget=forms.RadioSelect)
    notes = forms.CharField(required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 2}),
                            label="Notes for our team (optional)")
    agree_to_policies = forms.BooleanField(required=False, label="I agree to the policies above")

    def __init__(self, *args, needs_owner=False, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_method"].choices = [(m.code, m.name) for m in PaymentMethod.objects.filter(
            is_active=True)]
        if needs_owner:  # a domain is in the cart: the registry needs the owner's full postal details and phone
            for name, label in OWNER_FIELDS:
                self.fields[name] = forms.CharField(label=label, max_length=200, initial=getattr(client, name, "") if client else "")
            if client is not None:  # signed in: the phone and country may be missing too
                self.fields["phone"] = forms.CharField(label="Phone (with country code)", max_length=32, initial=client.phone)
                if not client.country:
                    self.fields["country"] = forms.ChoiceField(label="Country", choices=[("", "Choose your country"), *COUNTRIES])
            else:
                self.fields["phone"].required = True
        from apps.branding import services as branding

        if branding.get().policy_links:
            self.fields["agree_to_policies"].required = True
        else:  # nothing configured to agree to - do not even ask
            del self.fields["agree_to_policies"]


class StaffCheckoutForm(CheckoutForm):
    """Add Order only - a customer checking out their own cart always hears about it; here staff may place an
    order quietly (e.g. a phone order, or payment collected in person) and email the invoice later themselves
    (the "Email invoice" button on the invoice's own page). Nobody but the client can agree to the client's own
    policies, so this form never asks - ``services.checkout`` exempts a staff actor from the check outright."""

    notify_client = forms.BooleanField(required=False, initial=True, label="Email the client now")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("agree_to_policies", None)


class GuestCheckoutForm(CheckoutForm):
    """A visitor checking out: who they are (this creates their account), where they are (for tax), and how they will pay."""

    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150, required=False)
    company_name = forms.CharField(max_length=200, required=False, label="Company (optional)")
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    phone = forms.CharField(max_length=32, required=False)
    country = forms.ChoiceField(choices=[("", "Choose your country"), *COUNTRIES],
                                widget=forms.Select(attrs={"data-refresh": ""}),
                                help_text="Used for tax. The total updates when you change it.")
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), label="Choose a password",
                               help_text="At least 10 characters. Avoid common words and your email address.")
    password_confirm = forms.CharField(label="Confirm password",
                                       widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    field_order = ["first_name", "last_name", "company_name", "email", "phone", "country", "password", "password_confirm",
                   "payment_method", "notes"]

    def clean(self):
        data = super().clean()
        if data.get("password") and data.get("password") != data.get("password_confirm"):
            self.add_error("password_confirm", "Passwords do not match.")
        return data


class CancelOrderForm(forms.Form):
    reason = forms.CharField(required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 2}))


class StaffAddHostingForm(forms.Form):
    """Staff building an order on a client's behalf: they pick a plan from a list rather than a hidden id."""

    product = forms.ModelChoiceField(queryset=Product.objects.none())
    cycle = forms.ChoiceField(choices=[(c, BillingCycle(c).label) for c in STANDARD_CYCLE_MONTHS])
    domain = forms.CharField(label="Domain name", widget=forms.TextInput(attrs={"placeholder": "example.com"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(status=CatalogStatus.ACTIVE).exclude(
            whm_package_name="")


class StaffAddDomainForm(forms.Form):
    domain = forms.CharField(label="Domain to register", widget=forms.TextInput(attrs={"placeholder": "example.com"}))
    years = forms.IntegerField(min_value=1, initial=1)


class ReasonForm(forms.Form):
    reason = forms.CharField(required=False, max_length=500)


class OrderFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search")
    status = forms.ChoiceField(required=False, choices=[("", "All statuses"), *OrderStatus.choices])
