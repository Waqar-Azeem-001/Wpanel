from django import forms

from .models import StorefrontType


class StorefrontForm(forms.Form):
    name = forms.CharField(max_length=150, help_text='e.g. "Web Host Era UK".')
    site_url = forms.URLField(label="Website", help_text="The storefront's own address (https assumed).",
                              assume_scheme="https")
    integration_type = forms.ChoiceField(choices=StorefrontType.choices, initial=StorefrontType.WORDPRESS)
    webhook_url = forms.URLField(required=False, label="Webhook URL", assume_scheme="https",
                                 help_text="Where to ping this storefront when a published plan changes. Needs its own "
                                           "signing secret (generate one below) before anything is actually sent.")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
