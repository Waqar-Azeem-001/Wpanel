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
    domain = forms.CharField(label="Domain name", widget=forms.TextInput(attrs={"placeholder": "example.com"}))


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


class CheckoutForm(forms.Form):
    payment_method = forms.ChoiceField(widget=forms.RadioSelect)
    notes = forms.CharField(required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 2}),
                            label="Notes for our team (optional)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_method"].choices = [(m.code, m.name) for m in PaymentMethod.objects.filter(
            is_active=True)]


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
