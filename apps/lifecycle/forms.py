from django import forms

from .models import CancellationReason, LifecycleSettings, Timing


class CancellationForm(forms.Form):
    """Customer: ask to cancel. ``timing`` is left out for a domain (it can only end when it expires)."""

    reason_code = forms.ChoiceField(label="Why are you cancelling?", choices=CancellationReason.choices)
    reason_text = forms.CharField(label="Anything you would like us to know", required=False, max_length=1000,
                                  widget=forms.Textarea(attrs={"rows": 3}))
    timing = forms.ChoiceField(label="When should it end?", choices=Timing.choices, initial=Timing.END_OF_TERM)
    confirm = forms.BooleanField(label="I understand that when the service ends, its data and settings are removed "
                                       "and cannot be recovered.")

    def __init__(self, *args, domain=False, **kwargs):
        super().__init__(*args, **kwargs)
        if domain:
            del self.fields["timing"]
            self.fields["confirm"].label = ("I understand that this domain will not be renewed and will stop "
                                            "working when it expires.")


class ApproveForm(forms.Form):
    """Staff: approve a request, optionally with a refund."""

    timing = forms.ChoiceField(label="Ends", choices=Timing.choices)
    refund_amount = forms.DecimalField(label="Refund (optional)", required=False, min_value=0, max_digits=12,
                                       decimal_places=2, help_text="Only when the service ends immediately.")
    refund_payment = forms.ChoiceField(label="Refund from payment", required=False)
    note = forms.CharField(label="Note to the customer", required=False, max_length=1000,
                           widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, payments=(), domain=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["refund_payment"].choices = [("", "---------")] + [
            (str(tx.pk), f"{tx.invoice.number or tx.invoice_id} - {tx.method_name or 'Payment'} - {tx.currency} "
                         f"{available} refundable") for tx, available in payments]
        if domain:
            self.fields["timing"].choices = [(Timing.END_OF_TERM.value, Timing.END_OF_TERM.label)]
            del self.fields["refund_amount"], self.fields["refund_payment"]


class RejectForm(forms.Form):
    note = forms.CharField(label="Reason (shown to the customer)", max_length=1000,
                           widget=forms.Textarea(attrs={"rows": 3}))


class StaffCancelForm(CancellationForm):
    """Staff: end a service now, on the customer's behalf (creates and approves the request in one go)."""

    refund_amount = forms.DecimalField(label="Refund (optional)", required=False, min_value=0, max_digits=12,
                                       decimal_places=2, help_text="Only when the service ends immediately.")
    refund_payment = forms.ChoiceField(label="Refund from payment", required=False)
    note = forms.CharField(label="Note to the customer", required=False, max_length=1000,
                           widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, payments=(), domain=False, **kwargs):
        super().__init__(*args, domain=domain, **kwargs)
        self.fields["confirm"].label = "I have checked with the customer and want to end this service."
        self.fields["refund_payment"].choices = [("", "---------")] + [
            (str(tx.pk), f"{tx.invoice.number or tx.invoice_id} - {tx.method_name or 'Payment'} - {tx.currency} "
                         f"{available} refundable") for tx, available in payments]
        if domain:
            del self.fields["refund_amount"], self.fields["refund_payment"]


class SettingsForm(forms.ModelForm):
    class Meta:
        model = LifecycleSettings
        fields = ["grace_after_days", "suspend_after_days", "terminate_after_days", "auto_suspend", "auto_terminate",
                  "unsuspend_on_payment"]
