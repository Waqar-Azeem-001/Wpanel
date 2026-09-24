"""Plain forms (not ModelForm): validation only. The service layer applies changes."""
from django import forms

from .models import DnsRecordType


class AvailabilitySearchForm(forms.Form):
    domain = forms.CharField(label="Domain name", widget=forms.TextInput(attrs={"placeholder": "example.com"}))
    years = forms.IntegerField(min_value=1, initial=1)


class RegisterDomainForm(forms.Form):
    domain = forms.CharField(widget=forms.HiddenInput)
    years = forms.IntegerField(min_value=1, initial=1)
    nameservers = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}),
        help_text="One per line. Leave blank to use our defaults.",
    )

    def clean_nameservers(self):
        raw = self.cleaned_data.get("nameservers", "")
        return [line.strip() for line in raw.splitlines() if line.strip()]


class TransferDomainForm(forms.Form):
    domain = forms.CharField(label="Domain name")
    auth_code = forms.CharField(label="Authorization code")
    years = forms.IntegerField(min_value=1, initial=1)


class NameserversForm(forms.Form):
    nameservers = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), help_text="One per line, 2-13 total.")

    def clean_nameservers(self):
        raw = self.cleaned_data.get("nameservers", "")
        values = [line.strip() for line in raw.splitlines() if line.strip()]
        if not (2 <= len(values) <= 13):
            raise forms.ValidationError("Provide between 2 and 13 nameservers.")
        return values


class AutoRenewForm(forms.Form):
    auto_renew = forms.BooleanField(required=False)


class DnsRecordForm(forms.Form):
    record_type = forms.ChoiceField(choices=DnsRecordType.choices)
    name = forms.CharField(required=False, help_text="Subdomain part, blank for the root.")
    content = forms.CharField()
    ttl = forms.IntegerField(min_value=60, initial=3600)
    priority = forms.IntegerField(required=False, min_value=0, help_text="MX/SRV only.")


class RenewDomainForm(forms.Form):
    years = forms.IntegerField(min_value=1, initial=1)


class CancelDomainForm(forms.Form):
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class DomainFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search")
    status = forms.ChoiceField(required=False, choices=[])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import DomainStatus

        self.fields["status"].choices = [("", "All statuses"), *DomainStatus.choices]


class TldPricingForm(forms.Form):
    tld = forms.CharField(help_text='Including the dot, e.g. ".com".')
    register_price = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2)
    renew_price = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2)
    transfer_price = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2)
    redemption_price = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2, required=False, initial=0)
    min_years = forms.IntegerField(min_value=1, initial=1)
    max_years = forms.IntegerField(min_value=1, initial=10)

    def clean(self):
        data = super().clean()
        data["redemption_price"] = data.get("redemption_price") or 0
        return data


class TldStatusForm(forms.Form):
    is_active = forms.BooleanField(required=False)
