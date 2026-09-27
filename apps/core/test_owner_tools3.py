"""
Owner requests, third round (Phase E5): a better invoice PDF and invoice/quote emails with the PDF attached, where mail comes from and
where alerts go, several payment methods with real account details, the affiliate programme (payout account, applications), the
open-a-ticket page, the sign-in page, and the plan-limit rows.
"""
from decimal import Decimal

import pytest
from django.core import mail
from django.test import Client as HttpClient
from django.urls import reverse

from apps.accounts.roles import Role
from apps.billing import invoicing, payments, pdf
from apps.billing import services as billing
from apps.billing.models import PaymentMethod
from apps.clients import services as client_services
from apps.notifications import services as notifications
from apps.notifications.models import EmailMessage, NotificationSettings

pytestmark = pytest.mark.django_db

D = Decimal


def browser(user=None):
    client = HttpClient(HTTP_HOST="localhost", raise_request_exception=False)
    if user is not None:
        client.force_login(user)
    return client


@pytest.fixture
def boss(staff):
    return staff(Role.SUPER_ADMIN)


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def acme(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@acme.test",
                                                   "company_name": "Acme Ltd", "country": "US"})


@pytest.fixture
def owner_user(acme):
    return acme.contacts.get().user


def lines(*specs):
    return [{"description": d, "quantity": q, "unit_price": p} for d, q, p in specs]


def issued(manager, acme):
    return invoicing.issue_invoice(manager, invoicing.create_invoice(manager, acme, lines=lines(("Hosting", 1, "100.00"))))


def pdf_files(message):
    return [name for name, _content, kind in message.attachments if kind == "application/pdf"]


# --- Payment methods: bank, JazzCash, EasyPaisa, with real details ---------------------------------------------------------------------

def test_several_payment_methods_are_added_with_their_own_details_and_shown_to_customers(boss, owner_user):
    url = reverse("billing_staff:payment_method_save")
    for data in ({"kind": "bank", "name": "Meezan Bank", "bank_name": "Meezan Bank", "account_title": "Acme Hosting",
                  "account_number": "PK36MEZN0000123456789012", "swift_code": "MEZNPKKA", "instructions": "Use the invoice number.", "sort_order": "0"},
                 {"kind": "jazzcash", "name": "JazzCash", "account_title": "Acme Hosting", "account_number": "03001234567", "sort_order": "1"},
                 {"kind": "easypaisa", "name": "EasyPaisa", "account_title": "Acme Hosting", "account_number": "03111234567", "sort_order": "2"}):
        assert browser(boss).post(url, data).status_code == 302
    assert PaymentMethod.objects.count() == 3
    bank = PaymentMethod.objects.get(name="Meezan Bank")
    assert bank.code == "meezan-bank" and bank.swift_code == "MEZNPKKA"
    text = bank.how_to_pay
    assert "PK36MEZN0000123456789012" in text and "MEZNPKKA" in text and "Use the invoice number." in text
    page = browser(boss).get(reverse("billing_staff:payment_methods")).content.decode()
    assert "JazzCash" in page and "EasyPaisa" in page and "03001234567" in page
    assert "Add a payment method" in page or "Add a method" in page


def test_a_payment_method_is_edited_in_place_and_the_customer_sees_the_new_details(boss, owner_user):
    made = billing.save_payment_method(boss, "jc", name="JazzCash", kind="jazzcash", account_number="0300", account_title="Acme")
    edit_page = browser(boss).get(reverse("billing_staff:payment_methods") + f"?edit={made.pk}").content.decode()
    assert 'value="0300"' in edit_page
    browser(boss).post(reverse("billing_staff:payment_method_save"),
                       {"kind": "jazzcash", "name": "JazzCash", "code": "jc", "account_title": "Acme", "account_number": "03219999999", "sort_order": "0"})
    made.refresh_from_db()
    assert made.account_number == "03219999999" and PaymentMethod.objects.count() == 1


def test_a_method_with_no_details_is_left_off_the_pdf_and_one_with_details_is_on_it(boss, manager, acme):
    invoice = issued(manager, acme)
    assert b"HOW TO PAY" not in pdf.render_invoice_pdf(invoice)
    billing.save_payment_method(boss, "meezan", name="Meezan Bank", kind="bank", bank_name="Meezan", account_number="PK36MEZN0000", account_title="Acme")
    content = pdf.render_invoice_pdf(invoice)
    assert b"HOW TO PAY" in content and b"PK36MEZN0000" in content


# --- The invoice PDF -------------------------------------------------------------------------------------------------------------------

def test_an_unpaid_invoice_says_unpaid_and_how_to_pay_and_a_paid_one_says_paid_only(boss, manager, acme):
    billing.save_payment_method(boss, "meezan", name="Meezan Bank", kind="bank", bank_name="Meezan", account_number="PK36MEZN0000", account_title="Acme")
    invoice = issued(manager, acme)
    unpaid = pdf.render_invoice_pdf(invoice)
    assert b"UNPAID" in unpaid and b"HOW TO PAY" in unpaid and b"PAID" not in unpaid.replace(b"UNPAID", b"")
    payments.record_payment(manager, invoice, amount="100.00", reference="TT-9")
    invoice.refresh_from_db()
    paid = pdf.render_invoice_pdf(invoice)
    assert b"PAID" in paid and b"UNPAID" not in paid and b"HOW TO PAY" not in paid and b"TT-9" in paid


def test_the_pdf_takes_the_brand_name_and_stays_reproducible(boss, manager, acme):
    from apps.billing.models import BillingSettings

    row = BillingSettings.load()
    row.company_name = "Webhostera"
    row.save()
    invoice = issued(manager, acme)
    assert b"Webhostera" in pdf.render_invoice_pdf(invoice) and pdf.render_invoice_pdf(invoice) == pdf.render_invoice_pdf(invoice)


# --- Invoice and quote emails: layout and the attached PDF ---------------------------------------------------------------------------

def sent_email(template):
    return EmailMessage.objects.filter(template=template).order_by("-pk").first()


def test_the_invoice_email_has_the_layout_a_button_and_the_unpaid_pdf(manager, acme):
    invoice = issued(manager, acme)
    assert mail.outbox, "issuing an invoice sends an email"
    message = next(m for m in mail.outbox if invoice.number in m.subject)
    html = next(body for body, kind in message.alternatives if kind == "text/html")
    assert "View and pay the invoice" in html
    assert "<table" in html and invoice.number in html
    names = pdf_files(message)
    assert names == [f"{invoice.number}-unpaid.pdf"]
    assert next(c for n, c, k in message.attachments)[:4] == b"%PDF"


def test_the_payment_received_email_carries_the_paid_pdf(manager, acme):
    invoice = issued(manager, acme)
    mail.outbox.clear()
    payments.record_payment(manager, invoice, amount="100.00", reference="TT-1")
    received = [m for m in mail.outbox if pdf_files(m)]
    assert received, "the payment email should carry the invoice"
    assert pdf_files(received[0]) == [f"{invoice.number}-paid.pdf"]
    content = received[0].attachments[0][1]
    assert b"PAID" in content and b"UNPAID" not in content


def test_the_quote_email_carries_the_quote_pdf(manager, acme):
    quote = invoicing.send_quote(manager, invoicing.create_quote(manager, acme, lines=lines(("Build a site", 1, "500"))))
    message = next(m for m in mail.outbox if pdf_files(m))
    assert pdf_files(message) == [f"{quote.number}.pdf"] and b"QUOTE" in message.attachments[0][1]
    assert "Build a site" in message.body or "Build a site" in str(message.alternatives)


def test_an_email_without_a_document_has_no_attachment(owner_user):
    notifications.send_email(to_email=owner_user.email, template="order_placed", context={"user": owner_user}, user=owner_user)
    assert mail.outbox and not pdf_files(mail.outbox[-1])


def test_a_missing_document_never_stops_the_email(manager, acme, monkeypatch):
    from apps.billing import pdf as pdf_module

    invoice = issued(manager, acme)
    mail.outbox.clear()

    def boom(_invoice):
        raise RuntimeError("no fonts")

    monkeypatch.setattr(pdf_module, "render_invoice_pdf", boom)
    notifications.send_email(to_email="ada@acme.test", template="invoice_overdue", context={"invoice": invoice, "client": acme})
    assert mail.outbox and not pdf_files(mail.outbox[-1])


# --- Where mail comes from and where alerts go ---------------------------------------------------------------------------------------

def test_the_reply_to_and_alert_copies_are_saved_and_used(boss, manager, owner_user):
    saved = browser(boss).post(reverse("console:email_settings_save"), {
        "reply_to": "support@shop.test", "alert_emails": "owner@shop.test\nsales@shop.test", "alert_new_order": "on"})
    assert saved.status_code == 302
    row = NotificationSettings.load()
    assert row.reply_to == "support@shop.test" and row.addresses == ["owner@shop.test", "sales@shop.test"] and row.alert_new_order
    mail.outbox.clear()
    notifications.notify_team("order.new", "view_orders", title="New order", body="ORD-1", link="/staff/orders/")
    recipients = {r for m in mail.outbox for r in m.to}
    assert {"owner@shop.test", "sales@shop.test"} <= recipients
    copy = next(m for m in mail.outbox if "owner@shop.test" in m.to)
    assert "New order" in copy.subject and copy.reply_to == ["support@shop.test"]
    mail.outbox.clear()
    notifications.notify_team("payment.reported", "view_billing", title="Paid?", body="x")  # that alert is not ticked
    assert not any("owner@shop.test" in m.to for m in mail.outbox)


def test_a_mistyped_alert_address_is_refused_and_the_page_is_for_provider_managers_only(boss, manager, owner_user):
    bad = browser(boss).post(reverse("console:email_settings_save"), {"alert_emails": "not-an-address"})
    assert bad.status_code == 200 and not NotificationSettings.load().addresses
    assert browser(manager).post(reverse("console:email_settings_save"), {"reply_to": "x@y.test"}).status_code == 403
    assert browser(owner_user).post(reverse("console:email_settings_save"), {"reply_to": "x@y.test"}).status_code in (302, 403)
    page = browser(boss).get(reverse("console:email_providers")).content.decode()
    assert "Sending and receiving" in page and "Replies go to" in page


def test_a_new_order_and_a_new_account_alert_the_team(boss, manager):
    from apps.accounts import services as account_services

    NotificationSettings.objects.update_or_create(pk=1, defaults={"alert_emails": "owner@shop.test", "alert_new_client": True})
    mail.outbox.clear()
    account_services.register_user(email="newbie@example.test", password="Str0ng-Passw0rd!x", first_name="New", last_name="Bie")
    assert any("owner@shop.test" in m.to for m in mail.outbox)


# --- Affiliates: the payout account and the applications waiting --------------------------------------------------------------------

@pytest.fixture
def applicant(make_user):
    return make_user("aff@example.test")


def open_programme(require_approval):
    from apps.affiliates.models import AffiliateSettings

    row = AffiliateSettings.load()
    row.enabled, row.require_approval = True, require_approval
    row.save()


def test_someone_applies_with_a_payout_account_and_staff_see_it_and_the_waiting_application(boss, applicant):
    from apps.affiliates.models import Affiliate

    open_programme(require_approval=True)
    joined = browser(applicant).post(reverse("affiliates_customer:dashboard"), {
        "accept_terms": "on", "payout_method": "jazzcash", "payout_account_title": "Ali Khan", "payout_account_number": "03001234567"})
    assert joined.status_code == 302
    affiliate = Affiliate.objects.get(user=applicant)
    assert affiliate.status == "pending" and affiliate.payout_method == "jazzcash" and affiliate.has_payout_account
    assert "JazzCash" in affiliate.payout_summary and "03001234567" in affiliate.payout_summary
    tile = browser(boss).get(reverse("console:widget", args=["attention"])).content.decode()
    assert "Affiliate applications" in tile and "waiting for approval" in tile
    assert "03001234567" in browser(boss).get(reverse("affiliates_staff:affiliate", args=[affiliate.pk])).content.decode()
    assert any("New affiliate application" in n.title for n in boss.notifications.all())


def test_an_active_affiliate_gets_a_link_to_copy_and_share_and_can_change_their_payout_account(applicant):
    from apps.affiliates import services as affiliate_services

    open_programme(require_approval=False)
    affiliate = affiliate_services.enrol(applicant, accept_terms=True)
    page = browser(applicant).get(reverse("affiliates_customer:dashboard")).content.decode()
    assert f"/r/{affiliate.code}/" in page and "data-copy" in page and "Payout" in page
    saved = browser(applicant).post(reverse("affiliates_customer:details"), {
        "payout_method": "bank", "payout_bank_name": "Meezan", "payout_account_title": "Ali", "payout_account_number": "PK36MEZN0000"})
    assert saved.status_code == 302
    affiliate.refresh_from_db()
    assert "Bank account" in affiliate.payout_summary and "PK36MEZN0000" in affiliate.payout_summary


def test_an_account_number_needs_a_method(applicant):
    from apps.affiliates import services as affiliate_services
    from apps.core.exceptions import ServiceError

    open_programme(require_approval=False)
    affiliate = affiliate_services.enrol(applicant, accept_terms=True)
    with pytest.raises(ServiceError):
        affiliate_services.set_payout_account(applicant, affiliate, {"payout_account_number": "123"})


# --- The open-a-ticket page and the sign-in page ------------------------------------------------------------------------------------

def test_the_new_ticket_page_has_help_cards_beside_the_form(owner_user):
    page = browser(owner_user).get(reverse("support_customer:new")).content.decode()
    for text in ("Look for an answer first", "For a faster answer", "Search the help centre", "shop-layout"):
        assert text in page


def test_the_new_ticket_page_lists_the_customers_recent_tickets(owner_user, acme):
    from apps.support import services as support_services
    from apps.support.models import Department

    page = browser(owner_user).get(reverse("support_customer:new")).content.decode()
    assert "Your recent tickets" not in page  # none yet: the card is left out
    department = Department.objects.filter(is_active=True).first() or Department.objects.create(name="Sales", is_active=True)
    ticket = support_services.open_ticket(owner_user, acme, department=department, subject="Where is my invoice", body="Please resend")
    page = browser(owner_user).get(reverse("support_customer:new")).content.decode()
    assert "Your recent tickets" in page and ticket.reference in page and "Where is my invoice" in page


def test_the_sign_in_page_has_remember_me_and_the_help_column(customer):
    page = browser().get(reverse("accounts:login")).content.decode()
    assert "Keep me signed in" in page and "Not registered yet?" in page and "auth-brand" in page


def test_remember_me_keeps_the_session_for_thirty_days_and_otherwise_ends_with_the_browser(customer):
    from apps.accounts.views import REMEMBER_ME_SECONDS

    remembered = HttpClient(HTTP_HOST="localhost")
    remembered.post(reverse("accounts:login"), {"email": customer.email, "password": "Str0ng-Passw0rd!x", "remember": "on"})
    assert remembered.session.get_expiry_age() == REMEMBER_ME_SECONDS
    plain = HttpClient(HTTP_HOST="localhost")
    plain.post(reverse("accounts:login"), {"email": customer.email, "password": "Str0ng-Passw0rd!x"})
    assert plain.session.get_expire_at_browser_close()


# --- The confirm/reject buttons on a reported payment --------------------------------------------------------------------------------

def test_the_reported_payment_buttons_sit_side_by_side_and_confirm_pays_the_invoice(boss, manager, acme, owner_user):
    invoice = issued(manager, acme)
    method = billing.save_payment_method(boss, "bank", name="Bank", kind="bank", account_number="PK00")
    reported = payments.report_payment(owner_user, invoice, method=method, reference="TT-77", amount="100.00")
    page = browser(manager).get(reverse("billing_staff:invoice_detail", args=[invoice.pk])).content.decode()
    assert "Confirm" in page and "Reject" in page and "justify-content-end" in page
    assert browser(manager).post(reverse("billing_staff:transaction_confirm", args=[reported.pk])).status_code == 302
    invoice.refresh_from_db()
    assert invoice.status == "paid"


# --- Round 4 (E6): deleting a payment method, the footer, the invoice sender, the client picker, the ticket page -------------------------------

def test_a_payment_method_can_be_deleted_and_payments_made_with_it_keep_its_name(boss, manager, acme, owner_user):
    method = billing.save_payment_method(boss, "jc", name="JazzCash", kind="jazzcash", account_number="0300")
    invoice = issued(manager, acme)
    reported = payments.report_payment(owner_user, invoice, method=method, reference="TT-1", amount="100.00")
    page = browser(boss).get(reverse("billing_staff:payment_methods")).content.decode()
    assert reverse("billing_staff:payment_method_delete", args=[method.pk]) in page and "data-confirm" in page
    done = browser(boss).post(reverse("billing_staff:payment_method_delete", args=[method.pk]))
    assert done.status_code == 302 and not PaymentMethod.objects.filter(pk=method.pk).exists()
    reported.refresh_from_db()
    assert reported.payment_method_id is None and "JazzCash" in reported.method_name
    from apps.audit.models import AuditEvent

    assert AuditEvent.objects.filter(action="payment_method.deleted").exists()


def test_only_people_who_manage_billing_can_delete_a_payment_method(boss, staff):
    method = billing.save_payment_method(boss, "jc", name="JazzCash", kind="jazzcash", account_number="0300")
    agent = staff(Role.SUPPORT_AGENT)
    assert browser(agent).post(reverse("billing_staff:payment_method_delete", args=[method.pk])).status_code == 403
    assert PaymentMethod.objects.filter(pk=method.pk).exists()
    assert browser().post(reverse("billing_staff:payment_method_delete", args=[method.pk])).status_code == 302  # to sign in


def test_the_invoice_pdf_names_the_seller_from_the_brand_when_billing_settings_are_empty(manager, acme):
    from apps.branding import services as branding
    from apps.billing.models import BillingSettings

    BillingSettings.objects.update_or_create(pk=1, defaults={"company_name": "", "address": "", "email": "", "phone": ""})
    invoice = issued(manager, acme)
    content = pdf.render_invoice_pdf(invoice)
    assert branding.get().name.encode() in content
    BillingSettings.objects.filter(pk=1).update(company_name="Webhostera Ltd", address="Blue Area, Islamabad", email="billing@webhostera.pk", phone="+92 51 1")
    content = pdf.render_invoice_pdf(invoice)
    for expected in (b"Webhostera Ltd", b"Blue Area, Islamabad", b"billing@webhostera.pk", b"+92 51 1"):
        assert expected in content


def test_a_paid_invoice_is_marked_after_its_content_so_tables_cannot_hide_it(manager, acme):
    invoice = issued(manager, acme)
    payments.record_payment(manager, invoice, amount="100.00")
    invoice.refresh_from_db()
    content = pdf.render_invoice_pdf(invoice)
    assert content.rfind(b"PAID") > content.rfind(b"Amount due")  # the mark is the last thing drawn on the page


def test_the_client_footer_has_columns_and_the_staff_footer_is_slim(owner_user, boss):
    page = browser(owner_user).get(reverse("dashboard")).content.decode()
    assert page.count('class="foot-col"') == 3 and "Hosting plans" in page and "Open a ticket" in page and "Affiliate programme" in page
    staff_page = browser(boss).get(reverse("console:dashboard")).content.decode()
    assert "app-footer-slim" in staff_page and "foot-col" not in staff_page
    assert "site-footer" in browser().get(reverse("catalog:product_list")).content.decode()  # the store, signed out, has it too


def test_the_burger_is_only_drawn_for_a_phone_and_the_space_between_fields_is_not_doubled():
    from pathlib import Path

    css = Path("static/css/shell.css").read_text(encoding="utf-8")
    assert ".chrome-burger.chrome-burger { display: none; }" in css
    assert ".canvas .row > .field, .canvas .row > .field.form-check { margin-bottom: 0; }" in css


def test_the_new_invoice_page_lists_recent_clients_and_searches(manager, acme):
    page = browser(manager).get(reverse("billing_staff:invoice_new")).content.decode()
    assert "Recent clients" in page and "Acme Ltd" in page and f"client={acme.pk}" in page
    hit = browser(manager).get(reverse("billing_staff:invoice_new") + "?q=acme").content.decode()
    assert "matching" in hit and "Acme Ltd" in hit
    miss = browser(manager).get(reverse("billing_staff:invoice_new") + "?q=zzzz").content.decode()
    assert "No clients match" in miss


def test_the_customer_ticket_page_has_the_details_beside_the_conversation(owner_user, acme):
    from apps.support import services as support_services
    from apps.support.models import Department

    department = Department.objects.filter(is_active=True).first() or Department.objects.create(name="Sales", is_active=True)
    ticket = support_services.open_ticket(owner_user, acme, department=department, subject="Slow site", body="It is slow")
    page = browser(owner_user).get(reverse("support_customer:ticket", args=[ticket.pk])).content.decode()
    assert "Ticket details" in page and "While you wait" in page and "shop-layout" in page and "Close this ticket" in page
    assert page.index("Slow site") < page.index("Ticket details") and "It is slow" in page


def test_the_sign_in_card_is_centred_with_the_brand_above_and_no_dark_side_panel(customer):
    page = browser().get(reverse("accounts:login")).content.decode()
    assert "auth-center" in page and "auth-brand" in page and "auth-art" not in page.replace("auth-art-foot", "")
    assert page.index("auth-legal") < page.index("auth-alt")  # the small print sits above the "not registered yet" strip


def test_the_email_provider_page_is_two_columns_with_the_form_beside_the_lists(boss):
    page = browser(boss).get(reverse("console:email_providers")).content.decode()
    assert "wide-side" in page and "Add a provider" in page and "Sending and receiving" in page and "check-pill" in page
