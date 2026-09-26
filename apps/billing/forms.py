"""Plain forms (not ModelForm): validation only. The service layer applies changes."""
from decimal import Decimal

from django import forms

from apps.core import currencies

from .models import DiscountType


class PaymentMethodForm(forms.Form):
    code = forms.SlugField(max_length=50, help_text='Short identifier, e.g. "bank-transfer".')
    name = forms.CharField(max_length=100)
    instructions = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}),
                                   help_text="Shown to the customer at checkout and on their order.")
    sort_order = forms.IntegerField(min_value=0, initial=0)
    provider = forms.ModelChoiceField(queryset=None, required=False, empty_label="None (offline payment)",
                                      help_text="Lets customers pay this way online.")

    def __init__(self, *args, **kwargs):
        from .models import PaymentProvider

        super().__init__(*args, **kwargs)
        self.fields["provider"].queryset = PaymentProvider.objects.filter(is_active=True)


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


# --- Phase 07: invoices, quotes, payments --------------------------------------------------------

class LineForm(forms.Form):
    description = forms.CharField(max_length=255, required=False)
    quantity = forms.IntegerField(min_value=1, max_value=100000, initial=1, required=False)
    unit_price = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, required=False)
    taxable = forms.BooleanField(required=False, initial=True)

    def clean(self):
        data = super().clean()
        described, priced = bool(data.get("description")), data.get("unit_price") is not None
        if described != priced:  # a row is either empty (ignored) or complete
            self.add_error("unit_price" if described else "description",
                           "Enter a price." if described else "Enter a description.")
        if not data.get("quantity"):
            data["quantity"] = 1
        return data


LineFormSet = forms.formset_factory(LineForm, extra=3, max_num=100, validate_max=True)


def line_formset(data=None, existing=()):
    """A formset with the document's current lines pre-filled and a few blank rows to add more."""
    initial = [{"description": i.description, "quantity": i.quantity, "unit_price": i.unit_price,
                "taxable": i.taxable} for i in existing]
    return LineFormSet(data, initial=initial, prefix="lines")


def lines_from_formset(formset):
    return [{"description": f.cleaned_data["description"], "quantity": f.cleaned_data["quantity"],
             "unit_price": f.cleaned_data["unit_price"], "taxable": f.cleaned_data.get("taxable", False)}
            for f in formset if f.cleaned_data.get("description")]


class DocumentForm(forms.Form):
    """Fields common to a draft invoice and a draft quote."""

    currency = forms.ChoiceField(choices=currencies.CURRENCIES, label="Currency", required=False,
                                 widget=forms.Select(attrs={"data-currency-source": ""}),
                                 help_text="What the prices below are in. Amounts are not converted: choose it before you "
                                           "type the prices, and before you share the document.")

    def __init__(self, *args, current_currency="", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"].choices = currencies.choices(current_currency)
        if current_currency:
            self.fields["currency"].initial = current_currency

    discount_type = forms.ChoiceField(required=False, label="Discount",
                                      choices=[("", "No discount"), ("percent", "Percentage"), ("fixed", "Fixed amount")])
    discount_value = forms.DecimalField(required=False, min_value=0, max_digits=12, decimal_places=2,
                                        label="Discount value")
    discount_label = forms.CharField(required=False, max_length=100, label="Discount label")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}),
                            help_text="Shown to the customer on the document.")


class InvoiceForm(DocumentForm):
    pass


class QuoteForm(DocumentForm):
    valid_until = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}),
                                  help_text="Blank = the default validity period.")


class NewDocumentForm(forms.Form):
    client = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class RecordPaymentForm(forms.Form):
    amount = forms.DecimalField(label="Amount received", min_value=Decimal("0.01"), max_digits=12, decimal_places=2)
    method = forms.ModelChoiceField(label="Received via / into account", queryset=None, required=False,
                                    empty_label="Other / manual")
    reference = forms.CharField(label="Transaction ID / reference", required=False, max_length=200)
    received_on = forms.DateField(label="Date received", required=False, widget=forms.DateInput(attrs={"type": "date"}),
                                  help_text="Leave blank for today.")
    note = forms.CharField(required=False, max_length=500)
    idempotency_key = forms.CharField(required=False, max_length=100, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        from .models import PaymentMethod

        super().__init__(*args, **kwargs)
        self.fields["method"].queryset = PaymentMethod.objects.all()


class ReportPaymentForm(forms.Form):
    method = forms.CharField(max_length=50)
    amount = forms.DecimalField(required=False, min_value=Decimal("0.01"), max_digits=12, decimal_places=2)
    reference = forms.CharField(required=False, max_length=200)
    note = forms.CharField(required=False, max_length=500)


class PayOnlineForm(forms.Form):
    method = forms.CharField(max_length=50)


class RefundForm(forms.Form):
    amount = forms.DecimalField(required=False, min_value=Decimal("0.01"), max_digits=12, decimal_places=2,
                                help_text="Blank = the full remaining amount.")
    reason = forms.CharField(required=False, max_length=500)


class RejectForm(forms.Form):
    reason = forms.CharField(required=False, max_length=500)


class CancelForm(forms.Form):
    reason = forms.CharField(required=False, max_length=500)


class BillableItemForm(forms.Form):
    client = forms.IntegerField(min_value=1)
    description = forms.CharField(max_length=255)
    quantity = forms.IntegerField(min_value=1, max_value=100000, initial=1)
    unit_price = forms.DecimalField(min_value=Decimal("0.01"), max_digits=12, decimal_places=2)
    taxable = forms.BooleanField(required=False, initial=True)


class BillingSettingsForm(forms.Form):
    company_name = forms.CharField(required=False, max_length=200)
    address = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    email = forms.EmailField(required=False)
    phone = forms.CharField(required=False, max_length=32)
    tax_id = forms.CharField(required=False, max_length=50, label="Your tax ID")
    invoice_prefix = forms.CharField(required=False, max_length=10)
    quote_prefix = forms.CharField(required=False, max_length=10)
    payment_terms_days = forms.IntegerField(min_value=0, max_value=365, help_text="Days from issue until due.")
    quote_validity_days = forms.IntegerField(min_value=1, max_value=365)
    send_payment_reminders = forms.BooleanField(required=False, label="Send payment reminders",
                                                help_text="Email customers before an invoice is due and when it is overdue.")
    renewal_invoice_days = forms.IntegerField(min_value=0, max_value=90,
                                              help_text="Create renewal invoices this many days before expiry.")
    invoice_footer = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}),
                                     help_text="Printed on every invoice, e.g. bank details.")


class InvoiceFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search")
    status = forms.ChoiceField(required=False, choices=[])

    def __init__(self, *args, choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [("", "All"), *choices]
