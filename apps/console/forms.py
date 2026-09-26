"""Plain forms for the Setup screens (validation only; the services apply the changes)."""
import json

from django import forms

from apps.accounts.models import AccountStatus
from apps.accounts.roles import STAFF_ROLES, Role
from apps.domains.models import RegistrarProvider

STAFF_ROLE_CHOICES = [(r.value, r.label) for r in Role if r in STAFF_ROLES]


class StaffUserForm(forms.Form):
    email = forms.EmailField()
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    role = forms.ChoiceField(choices=STAFF_ROLE_CHOICES)

    def __init__(self, *args, allowed_roles=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_roles is not None:
            self.fields["role"].choices = [c for c in STAFF_ROLE_CHOICES if c[0] in allowed_roles]


class UserFilterForm(forms.Form):
    """The Users list filters (all optional; a value that is not a choice is ignored by the view, never an error)."""

    q = forms.CharField(required=False)
    status = forms.ChoiceField(required=False, choices=[("", "Any status"), *AccountStatus.choices])


class UserDetailsForm(forms.Form):
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    phone = forms.CharField(max_length=32, required=False)


class SetPasswordForm(forms.Form):
    new_password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}, render_value=False))
    new_password_confirm = forms.CharField(label="Confirm new password", widget=forms.PasswordInput(
        attrs={"autocomplete": "new-password"}, render_value=False))

    def clean(self):
        data = super().clean()
        if data.get("new_password") and data.get("new_password") != data.get("new_password_confirm"):
            self.add_error("new_password_confirm", "The two passwords do not match.")
        return data


class StaffRoleForm(forms.Form):
    role = forms.ChoiceField(choices=STAFF_ROLE_CHOICES)


class StaffStatusForm(forms.Form):
    status = forms.ChoiceField(choices=AccountStatus.choices)
    reason = forms.CharField(max_length=500, required=False)


class EmailProviderForm(forms.Form):
    name = forms.CharField(max_length=100)
    host = forms.CharField(max_length=255, help_text="e.g. smtp.example.com")
    port = forms.IntegerField(min_value=1, max_value=65535, initial=587)
    username = forms.CharField(max_length=255, required=False)
    password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False), strip=False,
                               help_text="Leave blank to keep the stored password. It is encrypted and never shown again.")
    use_tls = forms.BooleanField(required=False, initial=True, label="STARTTLS (usually port 587)")
    use_ssl = forms.BooleanField(required=False, label="Implicit TLS (usually port 465)")
    timeout = forms.IntegerField(min_value=1, max_value=120, initial=20, help_text="Seconds.")
    from_email = forms.CharField(max_length=255, help_text='e.g. "Web Host Era <billing@example.com>"')
    is_active = forms.BooleanField(required=False, label="Use this provider for all outgoing email")

    def clean(self):
        data = super().clean()
        if data.get("use_tls") and data.get("use_ssl"):
            raise forms.ValidationError("Use either STARTTLS or SSL, not both.")
        return data


class TestEmailForm(forms.Form):
    to_email = forms.EmailField(label="Send a test message to")


class RegistrarForm(forms.Form):
    name = forms.CharField(max_length=100)
    kind = forms.ChoiceField(choices=RegistrarProvider.Kind.choices)
    sandbox = forms.BooleanField(required=False, initial=True, label="Use the registrar's test (sandbox) service")
    credentials = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 4}),
        help_text='A JSON object such as {"api_key": "..."}; blank keeps what is stored. Never shown again. '
                  "The manual registrar needs none.")
    is_active = forms.BooleanField(required=False, label="Use this registrar for domains")

    def clean_credentials(self):
        raw = self.cleaned_data.get("credentials", "").strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            raise forms.ValidationError("Enter valid JSON.")
        if not isinstance(data, dict):
            raise forms.ValidationError("Must be a JSON object.")
        return data
