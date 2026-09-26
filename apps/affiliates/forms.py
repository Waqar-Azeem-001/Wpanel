from django import forms

from .models import Affiliate, AffiliateSettings, CommissionKind


class PayoutAccountForm(forms.Form):
    """Where a payout is sent: how you want to be paid, and the account. Optional when joining, needed before a payout."""

    payout_method = forms.ChoiceField(choices=[("", "Choose how you want to be paid"), *Affiliate.PayoutMethod.choices], required=False,
                                      label="How do you want to be paid?")
    payout_bank_name = forms.CharField(max_length=100, required=False, label="Bank name",
                                       help_text="For a bank account only.")
    payout_account_title = forms.CharField(max_length=150, required=False, label="Account title")
    payout_account_number = forms.CharField(max_length=64, required=False, label="Account number / IBAN / mobile number",
                                            help_text="The bank account or IBAN, or the JazzCash / EasyPaisa mobile number.")
    payout_details = forms.CharField(label="Anything else we should know", required=False, max_length=1000,
                                     widget=forms.Textarea(attrs={"rows": 2}))

    def clean(self):
        data = super().clean()
        if data.get("payout_account_number") and not data.get("payout_method"):
            self.add_error("payout_method", "Choose how you want to be paid.")
        return data


class JoinForm(PayoutAccountForm):
    accept_terms = forms.BooleanField(label="I have read and accept the programme terms")
    field_order = ["accept_terms", "payout_method", "payout_bank_name", "payout_account_title", "payout_account_number",
                   "payout_details"]


class PayoutDetailsForm(PayoutAccountForm):
    pass


class NoteForm(forms.Form):
    note = forms.CharField(label="Note (shown to the affiliate)", required=False, max_length=500,
                           widget=forms.Textarea(attrs={"rows": 2}))


class RequiredNoteForm(NoteForm):
    note = forms.CharField(label="Reason", max_length=500, widget=forms.Textarea(attrs={"rows": 2}))


class OverrideForm(forms.Form):
    kind = forms.ChoiceField(label="Commission type", required=False,
                             choices=[("", "Use the programme default")] + list(CommissionKind.choices))
    value = forms.DecimalField(label="Value", required=False, min_value=0, max_digits=12, decimal_places=2,
                               help_text="A percentage or an amount, by the type above.")


class CodeForm(forms.Form):
    code = forms.CharField(label="Referral code", max_length=32)


class PayoutForm(forms.Form):
    method = forms.CharField(label="Paid by", max_length=100, help_text="For example bank transfer or JazzCash.")
    reference = forms.CharField(label="Transaction ID / reference", required=False, max_length=200)
    paid_on = forms.DateField(label="Date paid", required=False, widget=forms.DateInput(attrs={"type": "date"}),
                              help_text="Leave blank for today.")
    note = forms.CharField(required=False, max_length=500)
    commissions = forms.MultipleChoiceField(label="Commissions to include", widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, payable=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["commissions"].choices = [
            (str(c.pk), f"{c.code} - {c.currency} {c.amount} (invoice {c.invoice.number or c.invoice_id})")
            for c in payable]
        self.fields["commissions"].initial = [str(c.pk) for c in payable]


class AttributeForm(forms.Form):
    code = forms.CharField(label="Affiliate code", max_length=32)


class SettingsForm(forms.ModelForm):
    class Meta:
        model = AffiliateSettings
        fields = ["enabled", "require_approval", "cookie_days", "commission_kind", "commission_value",
                  "recurring_months", "hold_days", "minimum_payout", "program_terms"]
        widgets = {"program_terms": forms.Textarea(attrs={"rows": 6})}
