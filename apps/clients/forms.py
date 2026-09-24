"""
Plain forms built from the model's field definitions. They validate input only;
the service layer applies changes (a ModelForm would mutate the instance during
validation, before the service can authorise and audit the change).
"""
from django import forms

from . import services
from .models import Client, ClientStatus, ContactRole


def _client_fields(names):
    return forms.fields_for_model(Client, fields=names)


class StaffClientForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.update(_client_fields(services.STAFF_FIELDS))
        self.fields["notes"].widget = forms.Textarea(attrs={"rows": 3})


class NewClientForm(StaffClientForm):
    owner_email = forms.EmailField(required=False, help_text="Owner login. Defaults to the client email. "
                                                             "New users get an email to set their password.")


class CustomerClientForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.update(_client_fields(services.CUSTOMER_FIELDS))


class ClientStatusForm(forms.Form):
    status = forms.ChoiceField(choices=ClientStatus.choices)
    reason = forms.CharField(required=False, max_length=500)


class AddContactForm(forms.Form):
    email = forms.EmailField()
    role = forms.ChoiceField(choices=ContactRole.choices, initial=ContactRole.TECHNICAL)
    first_name = forms.CharField(required=False, max_length=150)
    last_name = forms.CharField(required=False, max_length=150)


class ContactRoleForm(forms.Form):
    role = forms.ChoiceField(choices=ContactRole.choices)


class ClientFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search")
    status = forms.ChoiceField(required=False, choices=[("", "All statuses"), *ClientStatus.choices])
