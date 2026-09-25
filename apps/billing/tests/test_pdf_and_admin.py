"""PDF rendering, the integrity command, the provider admin form and tax exemption."""
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.billing import invoicing, payments, pdf
from apps.billing.admin import PaymentProviderForm
from apps.billing.models import BillingSettings, Invoice, PaymentProvider
from apps.clients import services as client_services

from .conftest import lines

pytestmark = pytest.mark.django_db

D = Decimal


def issued(manager, client_obj, *specs, **kwargs):
    specs = specs or (("Hosting", 1, "100.00"),)
    return invoicing.issue_invoice(manager, invoicing.create_invoice(
        manager, client_obj, lines=lines(*specs), **kwargs))


# --- PDF ------------------------------------------------------------------------------------------

def test_invoice_pdf_shows_the_stored_figures(manager, client_obj, vat):
    row = BillingSettings.load()
    row.company_name, row.invoice_footer = "Wpanel Ltd", "IBAN PK00 TEST"
    row.save()
    invoice = issued(manager, client_obj, ("Hosting", 2, "50.00"))
    payments.record_payment(manager, invoice, amount="40.00", reference="TT-5")
    content = pdf.render_invoice_pdf(Invoice.objects.get(pk=invoice.pk))
    assert content.startswith(b"%PDF")
    for expected in (b"INVOICE", b"INV-000001", b"Wpanel Ltd", b"Acme Ltd", b"Hosting", b"USD 110.00",
                     b"Sales tax", b"10%", b"Amount due", b"USD 70.00", b"TT-5", b"IBAN PK00 TEST"):
        assert expected in content, expected


def test_the_pdf_is_reproducible(manager, client_obj):
    invoice = issued(manager, client_obj)
    assert pdf.render_invoice_pdf(invoice) == pdf.render_invoice_pdf(invoice)


def test_awkward_text_never_breaks_the_pdf(manager, client_obj):
    invoice = issued(manager, client_obj, ("R&D <b>bold</b> & مرحبا 你好", 1, "5.00"),
                     notes="Line 1\nLine 2 <script>")
    content = pdf.render_invoice_pdf(invoice)
    assert content.startswith(b"%PDF") and b"R&amp;D" not in content  # markup was escaped, not interpreted


def test_quote_pdf_and_draft_pdf(manager, client_obj):
    quote = invoicing.send_quote(manager, invoicing.create_quote(manager, client_obj, lines=lines(("Build", 1, "10"))))
    assert b"QUOTE" in pdf.render_quote_pdf(quote) and b"QUO-000001" in pdf.render_quote_pdf(quote)
    draft = invoicing.create_invoice(manager, client_obj, lines=lines(("x", 1, "1")))
    assert b"Draft" in pdf.render_invoice_pdf(draft)


def test_many_lines_paginate(manager, client_obj):
    invoice = issued(manager, client_obj, *[(f"Item {i}", 1, "1.00") for i in range(95)])
    assert pdf.render_invoice_pdf(invoice).count(b"/Type /Page\n") >= 2


# --- verify_billing ---------------------------------------------------------------------------------

def test_verify_billing_reports_consistency_and_tampering(manager, client_obj, owner, bank):
    invoice = issued(manager, client_obj)
    payments.record_payment(manager, invoice, amount="30.00")
    out = StringIO()
    call_command("verify_billing", stdout=out)
    assert "consistent" in out.getvalue()

    Invoice.objects.filter(pk=invoice.pk).update(total=D("999.00"))
    with pytest.raises(CommandError):
        call_command("verify_billing", stderr=StringIO())


def test_verify_detects_a_gap_in_the_numbers(manager, client_obj):
    issued(manager, client_obj)
    second = issued(manager, client_obj)
    Invoice.objects.filter(pk=second.pk).update(number="INV-000009")
    with pytest.raises(CommandError):
        call_command("verify_billing", stderr=StringIO())


# --- Provider admin ---------------------------------------------------------------------------------

def test_the_admin_form_encrypts_secrets_and_never_shows_them():
    form = PaymentProviderForm(data={"name": "Gateway", "kind": "test", "sandbox": "on",
                                     "credentials": '{"api_key": "sk_live_abc"}', "webhook_secret": "whsec_1",
                                     "is_active": "on"})
    assert form.is_valid(), form.errors
    provider = form.save()
    provider.refresh_from_db()
    assert "sk_live_abc" not in provider.credentials_encrypted and "whsec_1" not in provider.webhook_secret_encrypted
    assert provider.get_credentials() == {"api_key": "sk_live_abc"} and provider.get_webhook_secret() == "whsec_1"

    rendered = str(PaymentProviderForm(instance=provider))
    assert "sk_live_abc" not in rendered and "whsec_1" not in rendered

    blank = PaymentProviderForm(instance=provider, data={"name": "Gateway 2", "kind": "test", "sandbox": "on",
                                                         "credentials": "", "webhook_secret": "", "is_active": "on"})
    assert blank.is_valid()
    blank.save()
    provider.refresh_from_db()
    assert provider.get_webhook_secret() == "whsec_1"  # blank keeps what is stored


def test_the_admin_refuses_an_active_test_gateway_when_disabled(settings):
    settings.ALLOW_TEST_PAYMENT_GATEWAY = False
    form = PaymentProviderForm(data={"name": "Gateway", "kind": "test", "sandbox": "on", "is_active": "on"})
    assert not form.is_valid()
    inactive = PaymentProviderForm(data={"name": "Gateway", "kind": "test", "sandbox": "on"})
    assert inactive.is_valid() and PaymentProvider.objects.count() == 0


def test_bad_credentials_json_is_rejected():
    for raw in ("not json", "[1, 2]"):
        assert not PaymentProviderForm(data={"name": "G", "kind": "test", "credentials": raw}).is_valid()


# --- Tax exemption ----------------------------------------------------------------------------------

def test_tax_exempt_is_staff_only(manager, client_obj, owner):
    client_services.update_client(owner, client_obj, {"tax_exempt": True, "first_name": "Ada"})
    client_obj.refresh_from_db()
    assert client_obj.tax_exempt is False  # a customer cannot grant themselves an exemption
    client_services.update_client(manager, client_obj, {"tax_exempt": True})
    client_obj.refresh_from_db()
    assert client_obj.tax_exempt is True


def test_tax_exempt_over_the_api(api, manager, owner, client_obj):
    api.force_authenticate(manager)
    assert api.patch(f"/api/v1/clients/{client_obj.pk}/", {"tax_exempt": True}).json()["tax_exempt"] is True
    api.force_authenticate(owner)
    body = api.get("/api/v1/me/clients/").json()
    row = body["results"][0] if isinstance(body, dict) and "results" in body else body[0]
    assert "tax_exempt" not in row
