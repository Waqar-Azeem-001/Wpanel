"""
Plain forms (not ModelForm): validation only. Note what is *absent*: no form
here has a price, total, discount, tax or expiry field - a browser can only
name what it wants (a product, a cycle, a domain), never what it costs.
"""
from django import forms

from apps.billing.models import PaymentMethod
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
