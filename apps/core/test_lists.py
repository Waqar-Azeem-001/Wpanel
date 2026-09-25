"""Phase D4c: the standard staff list (Showing X-Y of Z, bulk actions) and the confirmation modal on destructive actions."""
import re
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.contrib.messages import get_messages
from django.test import Client as HttpClient
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.billing.models import Invoice
from apps.core import bulk
from apps.support.models import Ticket

pytestmark = pytest.mark.django_db


def browser(user=None):
    client = HttpClient(HTTP_HOST="localhost", raise_request_exception=False)
    if user is not None:
        client.force_login(user)
    return client


def flashed(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


def follow_messages(client, response):
    """The messages a redirect leaves behind (they are shown on the next page)."""
    page = client.get(response.headers["Location"])
    return [str(m) for m in page.context["messages"]]


# --- Showing X-Y of Z -------------------------------------------------------------------------------------------------

def test_every_paged_list_says_how_many_rows_it_shows_of_how_many(world):
    admin = browser(world.people["admin"])
    page = admin.get(reverse("support_staff:tickets")).content.decode()
    assert re.search(r"Showing 1–6 of 6", page)
    page = admin.get(reverse("billing_staff:invoice_list")).content.decode()
    assert re.search(r"Showing 1–\d+ of \d+", page)
    customer = browser(world.people["customer owner"]).get(reverse("billing_customer:invoice_list")).content.decode()
    assert "Showing 1–" in customer


def test_the_summary_follows_the_filter_and_the_page(world):
    admin = browser(world.people["admin"])
    filtered = admin.get(reverse("support_staff:tickets"), {"status": "closed"}).content.decode()
    assert "Showing 1–1 of 1" in filtered
    for n in range(30):
        AuditEvent.objects.create(action="client.touched", target_type="clients.client", target_id=str(world.client.pk))
    second = admin.get(reverse("clients_staff:tab", args=[world.client.pk, "log"]), {"page": 2}).content.decode()
    assert re.search(r"Showing 26–\d+ of \d+", second) and "Page 2 of" in second


def test_an_empty_list_shows_no_summary_and_a_single_page_shows_no_paging(world):
    admin = browser(world.people["admin"])
    empty = admin.get(reverse("support_staff:tickets"), {"q": "zzz-nothing-matches"}).content.decode()
    assert "Showing" not in empty and "No tickets match" in empty
    one = admin.get(reverse("support_staff:tickets")).content.decode()
    assert "Showing 1–6 of 6" in one and 'aria-label="Pages"' not in one


def test_paging_links_keep_the_filters(world):
    for n in range(60):
        AuditEvent.objects.create(action="client.touched", target_type="clients.client", target_id=str(world.client.pk))
    page = browser(world.people["admin"]).get(reverse("clients_staff:tab", args=[world.client.pk, "log"])).content.decode()
    assert "?page=2" in page


# --- Bulk actions: tickets --------------------------------------------------------------------------------------------

def ticket_ids(world, *statuses):
    return [world.objects["tickets"][s].pk for s in statuses]


def bulk_post(user, url, data, next_url=None):
    client = browser(user)
    data = dict(data)
    if next_url:
        data["next"] = next_url
    return client, client.post(url, data)


def test_bulk_assign_resolve_close_and_priority_change_every_ticked_ticket(world):
    admin = world.people["admin"]
    url = reverse("support_staff:bulk")
    ids = ticket_ids(world, "open", "pending", "customer_reply")
    _, response = bulk_post(admin, url, {"ids": ids, "do": "assign_me"})
    assert response.status_code == 302 and set(Ticket.objects.filter(pk__in=ids).values_list("assigned_to", flat=True)) == {admin.pk}
    bulk_post(admin, url, {"ids": ids, "do": "resolve"})
    assert set(Ticket.objects.filter(pk__in=ids).values_list("status", flat=True)) == {"resolved"}
    bulk_post(admin, url, {"ids": ids, "do": "close"})
    assert set(Ticket.objects.filter(pk__in=ids).values_list("status", flat=True)) == {"closed"}
    bulk_post(admin, url, {"ids": ids, "do": "priority", "priority": "urgent"})
    assert set(Ticket.objects.filter(pk__in=ids).values_list("priority", flat=True)) == {"urgent"}


def test_only_the_ticked_tickets_change(world):
    others = ticket_ids(world, "agent_reply", "resolved")
    before = {t.pk: t.status for t in Ticket.objects.filter(pk__in=others)}
    bulk_post(world.people["admin"], reverse("support_staff:bulk"), {"ids": ticket_ids(world, "open"), "do": "close"})
    assert {t.pk: t.status for t in Ticket.objects.filter(pk__in=others)} == before


def test_each_change_is_audited_like_a_single_change(world):
    ids = ticket_ids(world, "open", "pending")
    before = AuditEvent.objects.count()
    bulk_post(world.people["admin"], reverse("support_staff:bulk"), {"ids": ids, "do": "priority", "priority": "high"})
    events = AuditEvent.objects.filter(action="ticket.priority_changed").order_by("-id")[:2]
    assert AuditEvent.objects.count() - before >= 2 and {e.target_id for e in events} == {str(i) for i in ids}
    assert all(e.actor == world.people["admin"] for e in events)


def test_one_ticket_that_cannot_change_does_not_stop_the_others_and_is_reported(world):
    client, response = bulk_post(world.people["admin"], reverse("support_staff:bulk"),
                                 {"ids": ticket_ids(world, "open", "closed", "pending"), "do": "resolve"})
    shown = follow_messages(client, response)
    resolved = Ticket.objects.get(pk=world.objects["tickets"]["open"].pk).status == "resolved"
    assert resolved and Ticket.objects.get(pk=world.objects["tickets"]["pending"].pk).status == "resolved"
    assert any("ticket(s) marked resolved" in m for m in shown)
    closed = world.objects["tickets"]["closed"]
    assert Ticket.objects.get(pk=closed.pk).status == "closed"  # a closed ticket cannot become resolved
    assert any("could not be changed" in m and closed.reference in m and "cannot become resolved" in m for m in shown), shown


def test_a_bad_action_or_no_selection_changes_nothing_and_says_so(world):
    before = Ticket.objects.get(pk=world.objects["tickets"]["open"].pk).status
    client, response = bulk_post(world.people["admin"], reverse("support_staff:bulk"), {"ids": ticket_ids(world, "open"), "do": "nonsense"})
    assert "Choose what to do" in " ".join(follow_messages(client, response))
    client, response = bulk_post(world.people["admin"], reverse("support_staff:bulk"), {"do": "close"})
    assert "Tick at least one row" in " ".join(follow_messages(client, response))
    assert Ticket.objects.get(pk=world.objects["tickets"]["open"].pk).status == before


def test_junk_and_foreign_ids_are_ignored_not_errors(world):
    client, response = bulk_post(world.people["admin"], reverse("support_staff:bulk"),
                                 {"ids": ["abc", "-1", "1e3", "", "99999999", *ticket_ids(world, "open")], "do": "close"})
    assert response.status_code == 302 and Ticket.objects.get(pk=world.objects["tickets"]["open"].pk).status == "closed"


def test_at_most_two_hundred_ids_are_acted_on_and_repeats_count_once():
    class Fake:
        def __init__(self, ids):
            self.POST = type("P", (), {"getlist": lambda self, name: ids})()

    ids = [str(n) for n in range(1, 500)] + ["5", "5"]
    found = bulk.selected_ids(Fake(ids))
    assert len(found) == bulk.MAX_SELECTED == 200 and found[:3] == [1, 2, 3] and len(set(found)) == 200


def test_bulk_needs_the_manage_permission_and_a_post(world):
    before = Ticket.objects.get(pk=world.objects["tickets"]["open"].pk).status
    url = reverse("support_staff:bulk")
    data = {"ids": ticket_ids(world, "open"), "do": "close"}
    assert browser().post(url, data).status_code == 302
    assert browser(world.people["customer owner"]).post(url, data).status_code == 403
    assert browser(world.people["admin"]).get(url).status_code == 405
    assert Ticket.objects.get(pk=world.objects["tickets"]["open"].pk).status == before


def test_the_return_address_is_the_filtered_list_but_never_another_site(world):
    admin = world.people["admin"]
    url = reverse("support_staff:bulk")
    _, mine = bulk_post(admin, url, {"ids": [], "do": "close"}, next_url="/staff/support/tickets/?status=open&page=2")
    assert mine.headers["Location"] == "/staff/support/tickets/?status=open&page=2"
    for hostile in ("//evil.example/x", "https://evil.example", "javascript:alert(1)", "\\\\evil.example"):
        _, response = bulk_post(admin, url, {"ids": [], "do": "close"}, next_url=hostile)
        assert response.headers["Location"] == reverse("support_staff:tickets"), hostile


def test_the_list_offers_bulk_actions_only_to_who_may_use_them(world):
    admin = browser(world.people["admin"]).get(reverse("support_staff:tickets")).content.decode()
    assert "With selected:" in admin and 'name="ids"' in admin and "data-select-all" in admin
    assert reverse("support_staff:bulk") in admin and 'data-confirm="Apply this to every ticked ticket?"' in admin
    reader = browser(world.people["support agent"]).get(reverse("support_staff:tickets")).content.decode()
    assert "With selected:" in reader  # agents manage support


# --- Bulk actions: invoices -------------------------------------------------------------------------------------------

def test_bulk_issue_turns_drafts_into_issued_invoices_and_reports_the_rest(world):
    admin = world.people["admin"]
    draft = world.objects["invoices"]["draft"]
    paid = world.objects["invoices"]["paid"]
    client, response = bulk_post(admin, reverse("billing_staff:invoice_bulk"), {"ids": [draft.pk, paid.pk], "do": "issue"})
    shown = follow_messages(client, response)
    assert Invoice.objects.get(pk=draft.pk).status == "unpaid" and Invoice.objects.get(pk=draft.pk).number
    assert any("invoice(s) issued" in m for m in shown) and any("could not be changed" in m for m in shown)
    assert Invoice.objects.get(pk=paid.pk).status == "paid"


def test_bulk_cancel_only_cancels_invoices_without_payments_and_records_the_reason(world):
    admin = world.people["admin"]
    unpaid = world.objects["invoices"]["unpaid"]
    partial = world.objects["invoices"]["partially_paid"]
    client, response = bulk_post(admin, reverse("billing_staff:invoice_bulk"),
                                 {"ids": [unpaid.pk, partial.pk], "do": "cancel", "reason": "Duplicate run"})
    assert Invoice.objects.get(pk=unpaid.pk).status == "cancelled" and Invoice.objects.get(pk=unpaid.pk).cancel_reason == "Duplicate run"
    assert Invoice.objects.get(pk=partial.pk).status == "partially_paid"  # money was taken: refund it instead
    assert any("could not be changed" in m for m in follow_messages(client, response))


def test_bulk_invoice_actions_need_manage_billing(world):
    url = reverse("billing_staff:invoice_bulk")
    data = {"ids": [world.objects["invoices"]["draft"].pk], "do": "issue"}
    assert browser(world.people["support agent"]).post(url, data).status_code == 403  # view_billing only
    assert browser(world.people["customer owner"]).post(url, data).status_code == 403
    assert Invoice.objects.get(pk=world.objects["invoices"]["draft"].pk).status == "draft"


def test_the_invoice_list_offers_bulk_actions_only_with_manage_billing(world):
    manager = browser(world.people["manager"]).get(reverse("billing_staff:invoice_list")).content.decode()
    assert "With selected:" in manager and "Issue drafts" in manager and 'name="ids"' in manager
    agent = browser(world.people["support agent"]).get(reverse("billing_staff:invoice_list")).content.decode()
    assert "With selected:" not in agent and 'name="ids"' not in agent


# --- The confirmation modal -------------------------------------------------------------------------------------------

def test_no_page_uses_the_browsers_own_confirm_box():
    root = Path(django_settings.BASE_DIR) / "templates"
    offenders = [str(p.relative_to(root)) for p in root.rglob("*.html") if "return confirm(" in p.read_text(encoding="utf-8")
                 or re.search(r'onsubmit="[^"]*confirm', p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_destructive_staff_actions_ask_first_in_the_modal(world):
    admin = browser(world.people["admin"])
    order = world.objects["orders"]["pending_payment"]
    page = admin.get(reverse("orders_staff:detail", args=[order.pk])).content.decode()
    assert 'data-confirm="' in page
    account = world.objects["accounts"]["active"]
    assert 'data-confirm="Terminate this hosting account' in admin.get(reverse("hosting_staff:detail", args=[account.pk])).content.decode()
    invoice = world.objects["invoices"]["unpaid"]
    assert "data-confirm=" in admin.get(reverse("billing_staff:invoice_detail", args=[invoice.pk])).content.decode()


def test_the_modal_script_handles_forms_and_the_select_all_box():
    js = (Path(django_settings.BASE_DIR) / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "data-confirm" in js and "data-select-all" in js and 'input[name=ids]' in js
