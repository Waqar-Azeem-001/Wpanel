"""
Test support for the link-integrity harness (roadmap Rule 5 / Phase D2).

`build_world()` creates one of everything the portal can show, in every status it can be in, for one customer, and returns
the people who can sign in. `Crawler` signs in as one of them and follows every link, form action, hx-get, script, image
and stylesheet it finds, the way a person would.
"""
import html
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from io import BytesIO

from django.contrib.auth.models import update_last_login
from django.contrib.auth.signals import user_logged_in
from django.contrib.staticfiles import finders
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as HttpClient
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.accounts.services import sync_role_membership
from apps.affiliates import services as affiliates
from apps.affiliates.models import Affiliate, AffiliateStatus, Commission, CommissionStatus, Payout, Referral
from apps.billing import invoicing, payments
from apps.branding import services as branding
from apps.billing import services as billing
from apps.billing.models import Coupon, Invoice, InvoiceStatus
from apps.clients import services as client_services
from apps.clients.models import ContactRole
from apps.domains import services as domains
from apps.domains.models import Domain, DomainStatus, RegistrarProvider
from apps.hosting import services as hosting
from apps.hosting.models import HostingAccount, HostingStatus
from apps.lifecycle import services as lifecycle
from apps.lifecycle.models import CancellationRequest, CancellationStatus
from apps.notifications.models import EmailMessage, Notification
from apps.orders import services as orders_services
from apps.orders.models import Order, OrderStatus
from apps.products import services as products
from apps.products.models import BillingCycle, ProductType
from apps.renewals import services as renewals
from apps.support import kb
from apps.support import services as support
from apps.support.models import Department, Ticket, TicketStatus

def _png(color="#123456"):
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
    return buffer.getvalue()


PASSWORD = "Str0ng-Passw0rd!x"
ROLE_NAMES = ["anonymous", "customer owner", "billing contact", "technical contact", "support agent", "manager", "admin",
              "super admin"]


@dataclass
class World:
    people: dict = field(default_factory=dict)  # role name -> User or None
    client: object = None
    objects: dict = field(default_factory=dict)  # a few named objects that tests want to look at


def _make_user(email, role):
    user = User.objects.create_user(email=email, password=PASSWORD, role=role)
    sync_role_membership(user)
    return user


def build_world():
    now = timezone.now()
    manager = _make_user("manager@harness.test", Role.MANAGER)
    admin = _make_user("admin@harness.test", Role.ADMIN)
    agent = _make_user("agent@harness.test", Role.SUPPORT_AGENT)
    root = User.objects.create_superuser(email="root@harness.test", password=PASSWORD)
    sync_role_membership(root)

    # --- the catalogue -----------------------------------------------------------------------------------------------
    server = products.create_server(manager, {"name": "srv1", "hostname": "srv1.harness.test"})
    product = products.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING,
                                                "whm_package_name": "starter_pkg"})
    products.set_price(manager, product, billing_cycle=BillingCycle.ANNUAL, price="100.00")
    products.set_product_servers(manager, product, [server.pk])
    products.set_product_status(manager, product, "active")
    addon = products.create_addon(manager, {"name": "Backups", "description": "Daily"})
    products.set_price(manager, addon, billing_cycle=BillingCycle.ANNUAL, price="20.00")
    RegistrarProvider.objects.create(name="Test", kind="manual", is_active=True)
    domains.set_tld_pricing(manager, ".com", register_price="12.00", renew_price="14.00", transfer_price="9.00")
    method = billing.save_payment_method(manager, "bank-transfer", name="Bank transfer")
    coupon = Coupon.objects.create(code="WELCOME10", discount_type="percent", value=Decimal("10"))

    # --- the customer and the people around them ---------------------------------------------------------------------
    client = client_services.create_client(manager, {"first_name": "Ada", "email": "ada@harness.test", "country": "US",
                                                     "company_name": "Ada Ltd"})
    owner = client.contacts.get(role=ContactRole.OWNER).user
    billing_user = client_services.add_contact(manager, client, email="finance@harness.test", role=ContactRole.BILLING).user
    tech_user = client_services.add_contact(manager, client, email="tech@harness.test", role=ContactRole.TECHNICAL).user

    # --- orders: every status ----------------------------------------------------------------------------------------
    orders = {}
    for i, status in enumerate(OrderStatus.values):
        orders[status] = Order.objects.create(
            client=client, currency="USD", subtotal=Decimal("100"), total=Decimal("100"), status=status,
            billing_name=client.display_name, billing_email=client.email, payment_method_name="Bank transfer")

    # --- invoices: every status, and the payments that go with them -------------------------------------------------
    def make(amount="100.00"):
        return invoicing.create_invoice(manager, client, lines=[{"description": "Hosting", "quantity": 1,
                                                                 "unit_price": amount}])

    invoices = {"draft": invoicing.create_draft_invoice(manager, client, lines=[
        {"description": "Draft", "quantity": 1, "unit_price": "10.00"}])}
    invoices["unpaid"] = invoicing.issue_invoice(manager, make())
    partial = invoicing.issue_invoice(manager, make("200.00"))
    part_tx = payments.record_payment(manager, partial, amount="50.00", method=method, reference="TXN-PART")
    invoices["partially_paid"] = Invoice.objects.get(pk=partial.pk)
    paid = invoicing.issue_invoice(manager, make("150.00"))
    paid_tx = payments.record_payment(manager, paid, amount="150.00", method=method, reference="TXN-PAID")
    invoices["paid"] = Invoice.objects.get(pk=paid.pk)
    cancelled = invoicing.issue_invoice(manager, make())
    invoicing.cancel_invoice(manager, cancelled, reason="Duplicate")
    invoices["cancelled"] = Invoice.objects.get(pk=cancelled.pk)
    refunded = invoicing.issue_invoice(manager, make("80.00"))
    refunded_tx = payments.record_payment(manager, refunded, amount="80.00", method=method, reference="TXN-REF")
    payments.refund_payment(manager, refunded_tx, amount="80.00", reason="Goodwill")
    invoices["refunded"] = Invoice.objects.get(pk=refunded.pk)
    overdue = invoicing.issue_invoice(manager, make("75.00"))
    Invoice.objects.filter(pk=overdue.pk).update(due_date=timezone.localdate() - timedelta(days=12))
    invoices["overdue"] = Invoice.objects.get(pk=overdue.pk)
    reported_invoice = invoicing.issue_invoice(manager, make("60.00"))
    pending_tx = payments.report_payment(owner, reported_invoice, method=method, amount="60.00", reference="TXN-PEND")
    failed_invoice = invoicing.issue_invoice(manager, make("45.00"))
    failed_tx = payments.report_payment(owner, failed_invoice, method=method, amount="45.00", reference="TXN-FAIL")
    payments.reject_payment(manager, failed_tx, reason="Not received")
    assert {i.status for i in invoices.values()} == set(InvoiceStatus.values), "an invoice status is missing from the world"

    quotes = {"draft": invoicing.create_quote(manager, client, lines=[
        {"description": "Quote", "quantity": 1, "unit_price": "300.00"}])}
    for name in ("sent", "accepted", "declined", "cancelled"):
        quote = invoicing.create_quote(manager, client, lines=[{"description": "Quote", "quantity": 1,
                                                                "unit_price": "300.00"}])
        invoicing.send_quote(manager, quote)
        if name == "accepted":
            invoicing.accept_quote(owner, quote)
        elif name == "declined":
            invoicing.decline_quote(owner, quote)
        elif name == "cancelled":
            invoicing.cancel_quote(manager, quote)
        quotes[name] = type(quote).objects.get(pk=quote.pk)
    item = invoicing.create_billable_item(manager, client, description="Setup help", unit_price="25.00")

    # --- services: hosting and domains in every status ---------------------------------------------------------------
    accounts = {}
    for i, status in enumerate(HostingStatus.values):
        account = hosting.request_hosting(manager, client, product, f"site{i}.harness.test")
        if status != HostingStatus.PENDING:
            HostingAccount.objects.filter(pk=account.pk).update(status=status)
        accounts[status] = HostingAccount.objects.get(pk=account.pk)
    live = accounts[HostingStatus.ACTIVE]
    renewals.set_hosting_term(manager, live, billing_cycle="annual", term_start=now - timedelta(days=350),
                              expires_at=now + timedelta(days=15), term_paid=Decimal("100.00"))
    domain_rows = {}
    for i, status in enumerate(DomainStatus.values):
        domain = domains.request_registration(manager, client, f"name{i}.com", 1)
        Domain.objects.filter(pk=domain.pk).update(status=status)
        domain_rows[status] = Domain.objects.get(pk=domain.pk)

    # --- cancellation requests: every status -------------------------------------------------------------------------
    cancellations = {}
    for status in CancellationStatus.values:
        service = hosting.request_hosting(manager, client, product, f"leaving-{status}.harness.test")
        HostingAccount.objects.filter(pk=service.pk).update(status=HostingStatus.ACTIVE)
        service.refresh_from_db()
        request = lifecycle.request_cancellation(owner, service, reason_code="too_expensive", timing="end_of_term")
        if status != CancellationStatus.PENDING:
            CancellationRequest.objects.filter(pk=request.pk).update(status=status)
        cancellations[status] = CancellationRequest.objects.get(pk=request.pk)

    # --- support -----------------------------------------------------------------------------------------------------
    department = Department.objects.get(slug="technical")
    tickets = {}
    for status in TicketStatus.values:
        ticket = support.open_ticket(owner, client, department=department, subject=f"Ticket {status}", body="Help please")
        support.reply(manager, ticket, "We are looking at it.")
        Ticket.objects.filter(pk=ticket.pk).update(status=status)
        tickets[status] = Ticket.objects.get(pk=ticket.pk)
    support.assign(manager, tickets["open"], agent)
    support.reply(owner, tickets["open"], "Here is a screenshot.", files=[SimpleUploadedFile("screen.png", _png())])
    canned = support.save_canned_reply(manager, None, title="Thanks", body="Thanks {{ name }}")
    category = kb.save_category(manager, None, name="Getting started")
    article = kb.save_article(manager, None, category=category, title="First steps", body="Welcome", is_published=True)
    kb.save_article(manager, None, category=category, title="Unpublished notes", body="Draft")

    # --- affiliates: every status, and commissions in every status ---------------------------------------------------
    affiliate_rows = {}
    for status in AffiliateStatus.values:
        client_for = (client if status == AffiliateStatus.ACTIVE else client_services.create_client(
            manager, {"first_name": status.title(), "email": f"{status}@harness.test", "country": "US"}))
        person = owner if status == AffiliateStatus.ACTIVE else client_for.contacts.get().user
        row = affiliates.enrol(person, accept_terms=True)
        Affiliate.objects.filter(pk=row.pk).update(status=status)
        affiliate_rows[status] = Affiliate.objects.get(pk=row.pk)
    active = affiliate_rows[AffiliateStatus.ACTIVE]
    payout = Payout.objects.create(affiliate=active, amount=Decimal("10"), currency="USD", method="Bank transfer",
                                   reference="PAYOUT-1", paid_on=timezone.localdate())
    commissions = {}
    for status in CommissionStatus.values:
        referred = client_services.create_client(manager, {"first_name": f"Ref{status}", "email": f"ref-{status}@harness.test",
                                                           "country": "US"})
        referral = Referral.objects.create(affiliate=active, client=referred)
        invoice = invoicing.issue_invoice(manager, invoicing.create_invoice(
            manager, referred, lines=[{"description": "Hosting", "quantity": 1, "unit_price": "100.00"}]))
        commissions[status] = Commission.objects.create(
            affiliate=active, referral=referral, invoice=invoice, status=status, currency="USD",
            base_amount=Decimal("100"), kind="percentage", rate=Decimal("10"), original_amount=Decimal("10"),
            amount=Decimal("10"), eligible_at=now,
            payout=payout if status == CommissionStatus.PAID else None)

    # --- an open cart (so the checkout page exists) and a brand logo ------------------------------------------------
    orders_services.add_hosting(owner, client, product, "cart-site.harness.test", "annual")
    branding.save_settings(admin, logo=_png(), favicon=_png("#654321"))

    # --- notifications, brand ----------------------------------------------------------------------------------------
    EmailMessage.objects.create(user=owner, event="invoice.issued", template="invoice_issued", to_email=owner.email,
                                subject="A message that could not be delivered", body_text="Hello",
                                status=EmailMessage.Status.FAILED, attempts=3, last_error="Connection refused")
    Notification.objects.create(user=owner, event="order.active", title="Your order is active", body="Thanks")
    Notification.objects.create(user=manager, event="order.active", title="New ticket", body="A customer wrote in")

    world = World(
        people={"anonymous": None, "customer owner": owner, "billing contact": billing_user,
                "technical contact": tech_user, "support agent": agent, "manager": manager, "admin": admin,
                "super admin": root},
        client=client)
    world.objects.update(orders=orders, invoices=invoices, quotes=quotes, accounts=accounts, domains=domain_rows,
                         cancellations=cancellations, tickets=tickets, affiliates=affiliate_rows, commissions=commissions,
                         payout=payout, coupon=coupon, addon=addon, product=product, server=server, method=method,
                         article=article, category=category, canned=canned, billable_item=item, part_tx=part_tx,
                         paid_tx=paid_tx, pending_tx=pending_tx, failed_tx=failed_tx, refunded_tx=refunded_tx)
    return world


# --- the crawler -----------------------------------------------------------------------------------------------------

class LinkParser(HTMLParser):
    """Everything in a page that names another URL: a[href], link[href], script/img/source[src], GET form[action], hx-get."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = []  # (kind, url)
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        if tag == "a" and a.get("name"):
            self.ids.add(a["name"])
        if tag == "a" and a.get("href") is not None:
            self.found.append(("a", a["href"]))
        elif tag == "link" and a.get("href"):
            self.found.append(("link", a["href"]))
        elif tag in ("script", "img", "source") and a.get("src"):
            self.found.append((tag, a["src"]))
        elif tag == "form" and (a.get("method") or "get").lower() == "get" and a.get("action") is not None:
            self.found.append(("form", a["action"]))
        if a.get("hx-get"):
            self.found.append(("hx-get", a["hx-get"]))


HOSTS = ("testserver", "localhost")
SKIP_PREFIXES = ("/api/", "/admin/")  # the API is not a page; the Django admin is not ours to crawl into
NON_HTTP = ("mailto:", "tel:", "javascript:", "data:", "sms:")


def internal_target(url, base_path):
    """The path (with query) a link points to on this site, or None if it leaves the site or is not a web link."""
    if url is None or url.startswith(NON_HTTP):
        return None
    parsed = urlparse(urljoin(base_path, url))
    if parsed.netloc and parsed.netloc not in HOSTS:
        return None
    path = parsed.path or "/"
    return path + (f"?{parsed.query}" if parsed.query else "")


def dead_anchor(url, ids):
    """A link that goes nowhere: empty, ``#``, ``javascript:``, or ``#name`` where the page has no such id."""
    if url in ("", "#") or url.lower().startswith("javascript:"):
        return True
    return url.startswith("#") and url[1:] not in ids


@dataclass
class Finding:
    role: str
    path: str
    status: int
    found_on: str
    kind: str


@contextmanager
def _quiet_last_login():
    """Signing in for a crawl must not change ``last_login``: that would quietly invalidate the person's emailed reset link."""
    user_logged_in.disconnect(update_last_login, dispatch_uid="update_last_login")
    try:
        yield
    finally:
        user_logged_in.connect(update_last_login, dispatch_uid="update_last_login")


class Crawler:
    """Signs in as one person and follows every link on the site from the home page."""

    def __init__(self, role, user, *, limit=4000):
        self.role, self.user, self.limit = role, user, limit
        self.client = HttpClient(HTTP_HOST="localhost", raise_request_exception=False)
        if user is not None:
            with _quiet_last_login():
                self.client.force_login(user)
        self.visited = {}  # path -> status
        self.findings = []
        self.reached_names = set()
        self.skipped = []

    def _record(self, path, status, found_on, kind):
        self.visited[path] = status
        if status >= 400 or status == 0:
            self.findings.append(Finding(self.role, path, status, found_on, kind))

    def run(self, start="/"):
        from django.urls import Resolver404, resolve

        queue = [(start, "(start)", "start")]
        while queue and len(self.visited) < self.limit:
            path, found_on, kind = queue.pop(0)
            bare = path.split("#")[0]
            if not bare or bare in self.visited:
                continue
            route = urlparse(bare).path
            if route.startswith(SKIP_PREFIXES):
                self.skipped.append(bare)
                continue
            if route.startswith("/static/"):
                found = bool(finders.find(route[len("/static/"):]))
                self._record(bare, 200 if found else 404, found_on, kind)
                continue
            if route.startswith("/media/"):
                self.visited[bare] = 200
                continue
            response = self.client.get(bare, follow=False)
            self._record(bare, response.status_code, found_on, kind)
            try:
                name = resolve(route).view_name
                if response.status_code < 400:
                    self.reached_names.add(name)
            except Resolver404:
                pass
            if response.status_code in (301, 302):
                target = internal_target(response.get("Location", ""), bare)
                if target:
                    queue.append((target, f"redirect from {bare}", "redirect"))
                continue
            if response.status_code == 200 and response.get("Content-Type", "").startswith("text/html"):
                parser = LinkParser()
                parser.feed(response.content.decode("utf-8", "replace"))
                for found_kind, url in parser.found:
                    if found_kind == "a" and dead_anchor(url, parser.ids):
                        self.findings.append(Finding(self.role, url or "(empty)", 0, bare, "dead link"))
                        continue
                    target = internal_target(url, bare)
                    if target:
                        queue.append((target, bare, found_kind))
        return self

    def problems(self):
        """Broken links: a 404 or 5xx anywhere, or a 403 (a link to a page this person may not open)."""
        return [f for f in self.findings]


def crawl_everyone(world, roles=ROLE_NAMES):
    return {role: Crawler(role, world.people[role]).run() for role in roles}


def email_links(messages):
    """(message, url) for every web address in the plain-text and HTML parts of the emails."""
    found = []
    for message in messages:
        parts = [message.body] + [content for content, _type in getattr(message, "alternatives", [])]
        for text in parts:
            for url in re.findall(r"https?://[^\s\"'<>)]+", html.unescape(text)):
                found.append((message, url.rstrip(".,")))
    return found
