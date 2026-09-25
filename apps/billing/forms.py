"""Plain forms (not ModelForm): validation only. The service layer applies changes."""
from decimal import Decimal

from django import forms

from .models import DiscountType


class PaymentMethodForm(forms.Form):
    code = forms.SlugField(max_length=50, help_text='Short identifier, e.g. "bank-transfer".')
    name = forms.CharField(max_length=100)
    instructions = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}),
                                   help_text="Shown to the customer at checkout and on their order.")
    sort_order = forms.IntegerField(min_value=0, initial=0)


class TaxRuleForm(forms.Form):
    country = forms.CharField(required=False, max_length=2,
                              help_text="Two-letter code, e.g. PK. Blank = the default rule for all other countries.")
    name = forms.CharField(max_length=100, help_text='e.g. "VAT" or "GST".')
    rate = forms.DecimalField(min_value=0, max_value=100, max_digits=5, decimal_places=2,
                              help_text="Percentage, e.g. 15.00.")

    def clean_country(self):
        return self.cleaned_data.get("country", "").strip().upper()


class CouponForm(forms.Form):
    code = forms.CharField(max_length=50)
    description = forms.CharField(required=False, max_length=200)
    discount_type = forms.ChoiceField(choices=DiscountType.choices)
    value = forms.DecimalField(min_value=Decimal("0.01"), max_digits=10, decimal_places=2)
    valid_from = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"},
                                                                                format="%Y-%m-%dT%H:%M"),
                                     input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"])
    valid_until = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"},
                                                                                 format="%Y-%m-%dT%H:%M"),
                                      input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"])
    max_redemptions = forms.IntegerField(required=False, min_value=1, help_text="Blank = unlimited.")
    one_per_client = forms.BooleanField(required=False)
    min_subtotal = forms.DecimalField(required=False, min_value=0, max_digits=10, decimal_places=2, initial=0)

    def clean(self):
        data = super().clean()
        data["min_subtotal"] = data.get("min_subtotal") or 0
        return data
