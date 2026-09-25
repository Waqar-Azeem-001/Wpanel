from django import forms

from .models import AffiliateSettings, CommissionKind


class JoinForm(forms.Form):
    accept_terms = forms.BooleanField(label="I have read and accept the programme terms")
    payout_details = forms.CharField(
        label="Where should we send your payouts? (optional now)", required=False, max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="For example your bank account or wallet number. You can add or change this later.")


class PayoutDetailsForm(forms.Form):
    payout_details = forms.CharField(label="Payout details", required=False, max_length=1000,
                                     widget=forms.Textarea(attrs={"rows": 3}))


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
