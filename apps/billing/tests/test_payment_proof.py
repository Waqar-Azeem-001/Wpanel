"""A customer can attach a screenshot or PDF to the payment they report; staff and the customer can download it, nobody else can."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.billing import payments
from apps.billing.models import PaymentMethod, Transaction
from apps.core.exceptions import ServiceError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup(client, customer, staff):
    from apps.accounts.roles import Role
    from apps.billing import invoicing
    from apps.clients import services as client_services

    manager = staff(Role.MANAGER)
    client_obj = client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})
    owner = client_obj.contacts.get().user
    invoice = invoicing.create_invoice(manager, client_obj, lines=[{"description": "Hosting", "quantity": 1, "unit_price": "100.00"}])
    invoice = invoicing.issue_invoice(manager, invoice)
    method = PaymentMethod.objects.create(code="bank", name="Bank", is_active=True)
    return client, owner, manager, invoice, method


def test_a_reported_payment_keeps_its_proof_and_both_sides_can_download_it(setup):
    client, owner, manager, invoice, method = setup
    tx = payments.report_payment(owner, invoice, method=method, proof=SimpleUploadedFile("receipt.png", PNG))
    assert tx.proof and tx.proof_name == "receipt.png" and tx.proof_content_type == "image/png"
    client.force_login(owner)
    mine = client.get(reverse("billing_customer:payment_proof", args=[tx.pk]))
    assert mine.status_code == 200 and "attachment" in mine["Content-Disposition"] and mine["X-Content-Type-Options"] == "nosniff"
    client.force_login(manager)
    assert client.get(reverse("billing_staff:transaction_proof", args=[tx.pk])).status_code == 200
    assert "Proof" in client.get(reverse("billing_staff:invoice_detail", args=[invoice.pk])).content.decode()


def test_a_stranger_cannot_download_it_and_a_payment_without_proof_has_nothing_to_download(setup, customer):
    client, owner, manager, invoice, method = setup
    tx = payments.report_payment(owner, invoice, method=method, proof=SimpleUploadedFile("receipt.png", PNG))
    client.force_login(customer)
    assert client.get(reverse("billing_customer:payment_proof", args=[tx.pk])).status_code == 404
    client.logout()
    assert client.get(reverse("billing_customer:payment_proof", args=[tx.pk])).status_code == 302
    plain = Transaction.objects.create(invoice=invoice, client=invoice.client, amount="1.00", currency=invoice.currency)
    client.force_login(manager)
    assert client.get(reverse("billing_staff:transaction_proof", args=[plain.pk])).status_code == 404


@pytest.mark.parametrize("name,content", [("virus.exe", b"MZ\x90"), ("fake.png", b"not a png"), ("empty.png", b"")])
def test_a_bad_proof_is_refused_and_nothing_is_recorded(setup, name, content):
    client, owner, manager, invoice, method = setup
    with pytest.raises(ServiceError):
        payments.report_payment(owner, invoice, method=method, proof=SimpleUploadedFile(name, content))
    assert not Transaction.objects.filter(invoice=invoice).exists()


def test_the_invoice_page_form_uploads_the_proof(setup):
    client, owner, manager, invoice, method = setup
    client.force_login(owner)
    page = client.get(reverse("billing_customer:invoice_detail", args=[invoice.pk])).content.decode()
    assert 'enctype="multipart/form-data"' in page and 'name="proof"' in page
    client.post(reverse("billing_customer:invoice_pay", args=[invoice.pk]),
                {"method": "bank", "proof": SimpleUploadedFile("slip.png", PNG)})
    assert Transaction.objects.get(invoice=invoice).proof_name == "slip.png"
