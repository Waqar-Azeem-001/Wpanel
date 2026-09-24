"""Plain forms (not ModelForm): validation only. The service layer applies changes."""
from django import forms

from apps.products.models import CatalogStatus, Product, Server


class RequestHostingForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.filter(status=CatalogStatus.ACTIVE))
    domain = forms.CharField(label="Domain name", widget=forms.TextInput(attrs={"placeholder": "example.com"}))


class AssignServerForm(forms.Form):
    server = forms.ModelChoiceField(queryset=Server.objects.all())

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)
        if product is not None and product.servers.exists():
            self.fields["server"].queryset = product.servers.all()


class CancelHostingForm(forms.Form):
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class SuspendHostingForm(forms.Form):
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class TerminateHostingForm(forms.Form):
    keep_dns = forms.BooleanField(required=False, label="Keep DNS entries")


class ChangePackageForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.exclude(whm_package_name=""), label="New product")


class HostingFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search")
    status = forms.ChoiceField(required=False, choices=[])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import HostingStatus

        self.fields["status"].choices = [("", "All statuses"), *HostingStatus.choices]
