"""Plain forms: validation only. Nothing here carries a price, credit, expiry or total."""
from decimal import Decimal

from django import forms

from apps.products.models import BillingCycle

from .services import RECURRING_CYCLES


class RenewDomainForm(forms.Form):
    years = forms.IntegerField(min_value=1, max_value=10, initial=1)


class UpgradeForm(forms.Form):
    product = forms.IntegerField(min_value=1)


class HostingTermForm(forms.Form):
    billing_cycle = forms.ChoiceField(choices=[(c.value, c.label) for c in map(BillingCycle, RECURRING_CYCLES)])
    custom_months = forms.IntegerField(required=False, min_value=0, max_value=120,
                                       help_text="Only for the Custom cycle.")
    term_start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}),
                                 help_text="When the current paid term began.")
    expires_at = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Paid through",
                                 help_text="The service is paid up to this date.")
    term_paid = forms.DecimalField(min_value=Decimal("0"), max_digits=12, decimal_places=2,
                                   label="Amount paid for this term",
                                   help_text="Excluding tax and setup fees. This is the most an upgrade credit can be worth.")


class NoteForm(forms.Form):
    note = forms.CharField(required=False, max_length=500)
