"""Support pages, the attachment download, the help centre and the API."""
import pytest
from django.core import mail

from apps.accounts.roles import Role
from apps.support import kb, services
from apps.support.models import (Department, KBCategory, Ticket, TicketAttachment, TicketPriority, TicketStatus)

from .conftest import PDF, PNG, upload

pytestmark = pytest.mark.django_db


def open_ticket(owner, client_obj, technical, **kwargs):
    kwargs.setdefault("subject", "My site is down")
    kwargs.setdefault("body", "It shows a 500 error.")
    return services.open_ticket(owner, client_obj, department=technical, **kwargs)


def reload(obj):
    obj.refresh_from_db()
    return obj


# --- Access ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/account/support/", "/account/support/new/", "/staff/support/", "/staff/support/tickets/",
                                  "/staff/support/departments/", "/staff/support/replies/", "/staff/support/kb/"])
def test_pages_require_login(client, path):
    assert client.get(path).status_code == 302


def test_customers_cannot_open_staff_support(client, owner):
    client.force_login(owner)
    for path in ("/staff/support/", "/staff/support/tickets/", "/staff/support/departments/"):
        assert client.get(path).status_code == 403


def test_the_help_centre_is_public(client):
    assert client.get("/help/").status_code == 200


# --- Customer pages ------------------------------------------------------------------------------------------

def test_a_customer_opens_a_ticket_with_an_attachment_and_replies(client, owner, client_obj, technical, agent, mail_outbox=None):
    client.force_login(owner)
    page = client.get("/account/support/new/")
    assert page.status_code == 200 and b"Technical Support" in page.content and b'type="file"' in page.content
    assert b"multiple" in page.content and b"Urgent" not in page.content  # customers cannot pick urgent
    response = client.post("/account/support/new/", {
        "department": technical.pk, "subject": "Email bouncing", "priority": "normal",
        "body": "My mail bounces.", "files": [upload("bounce.png"), upload("log.txt", b"550 rejected")]})
    ticket = Ticket.objects.get()
    assert response.status_code == 302 and response["Location"] == f"/account/support/{ticket.pk}/"
    assert TicketAttachment.objects.filter(message__ticket=ticket).count() == 2

    page = client.get(response["Location"]).content
    assert b"Email bouncing" in page and b"My mail bounces." in page and b"bounce.png" in page
    client.post(f"/account/support/{ticket.pk}/", {"body": "Any news?"})
    assert reload(ticket).status == TicketStatus.CUSTOMER_REPLY and ticket.messages.count() == 2
    listing = client.get("/account/support/").content
    assert ticket.reference.encode() in listing and b"Customer Reply" in listing


def test_a_bad_upload_is_reported_and_nothing_is_created(client, owner, technical):
    client.force_login(owner)
    response = client.post("/account/support/new/", {
        "department": technical.pk, "subject": "x", "priority": "normal", "body": "y",
        "files": [upload("evil.exe", b"MZ" + b"0" * 30)]})
    assert response.status_code == 200 and b"not an allowed file type" in response.content
    assert not Ticket.objects.exists()


def test_customers_never_see_internal_notes_on_the_page(client, owner, client_obj, technical, agent):
    ticket = open_ticket(owner, client_obj, technical)
    services.reply(agent, ticket, "INTERNAL-SECRET-NOTE", internal=True)
    services.reply(agent, ticket, "A public answer.")
    client.force_login(owner)
    page = client.get(f"/account/support/{ticket.pk}/").content
    assert b"A public answer." in page and b"INTERNAL-SECRET-NOTE" not in page and b"Internal note" not in page
    client.force_login(agent)
    staff_page = client.get(f"/staff/support/tickets/{ticket.pk}/").content
    assert b"INTERNAL-SECRET-NOTE" in staff_page and b"Internal note" in staff_page


def test_a_strangers_ticket_is_a_404_and_replying_is_impossible(client, customer, owner, client_obj, technical):
    ticket = open_ticket(owner, client_obj, technical)
    client.force_login(customer)
    assert client.get(f"/account/support/{ticket.pk}/").status_code == 404
    assert client.post(f"/account/support/{ticket.pk}/", {"body": "hi"}).status_code == 404
    assert client.post(f"/account/support/{ticket.pk}/close/").status_code == 404
    assert ticket.reference.encode() not in client.get("/account/support/").content


def test_closing_and_the_closed_tab(client, owner, client_obj, technical):
    ticket = open_ticket(owner, client_obj, technical)
    client.force_login(owner)
    client.post(f"/account/support/{ticket.pk}/close/")
    assert reload(ticket).status == TicketStatus.CLOSED
    assert ticket.reference.encode() not in client.get("/account/support/").content
    assert ticket.reference.encode() in client.get("/account/support/?view=closed").content
    assert b"Reopen with a reply" in client.get(f"/account/support/{ticket.pk}/").content


def test_a_customer_with_no_account_is_told_support_is_for_customers(client, manager):
    from apps.accounts.models import User

    user = User.objects.create_user(email="nobody@example.com", password="Str0ng-Passw0rd!x")
    client.force_login(user)
    assert b"available to customer accounts" in client.get("/account/support/new/").content


def test_related_hosting_belongs_to_the_client(client, owner, client_obj, other_client, technical, manager):
    from apps.hosting.models import HostingAccount
    from apps.products import services as product_services
    from apps.products.models import ProductType

    product = product_services.create_product(manager, {"name": "P", "type": ProductType.SHARED_HOSTING,
                                                        "whm_package_name": "p"})
    mine = HostingAccount.objects.create(client=client_obj, product=product, domain="mine.com", username="mine")
    theirs = HostingAccount.objects.create(client=other_client, product=product, domain="theirs.com", username="theirs")
    client.force_login(owner)
    page = client.get("/account/support/new/").content
    assert b"mine.com" in page and b"theirs.com" not in page
    ok = client.post("/account/support/new/", {"department": technical.pk, "subject": "s", "priority": "low",
                                               "body": "b", "hosting_account": mine.pk})
    assert ok.status_code == 302 and Ticket.objects.get().hosting_account == mine
    bad = client.post("/account/support/new/", {"department": technical.pk, "subject": "s", "priority": "low",
                                                "body": "b", "hosting_account": theirs.pk})
    assert bad.status_code == 200 and Ticket.objects.count() == 1


# --- Attachment downloads ------------------------------------------------------------------------------------

def test_attachments_download_only_as_attachments_only_to_those_allowed(client, owner, customer, agent, client_obj, technical):
    ticket = open_ticket(owner, client_obj, technical, files=[upload("proof.pdf", PDF)])
    note = services.reply(agent, ticket, "internal file", internal=True, files=[upload("private.png", PNG)])
    public = TicketAttachment.objects.get(message__ticket=ticket, message__is_internal=False)
    private = TicketAttachment.objects.get(message=note)

    client.force_login(owner)
    response = client.get(f"/account/support/attachments/{public.pk}/")
    assert response.status_code == 200 and b"".join(response.streaming_content).startswith(b"%PDF")
    assert "attachment" in response["Content-Disposition"] and "proof.pdf" in response["Content-Disposition"]
    assert response["X-Content-Type-Options"] == "nosniff" and response["Content-Type"] == "application/pdf"
    assert "no-store" in response["Cache-Control"]
    assert client.get(f"/account/support/attachments/{private.pk}/").status_code == 404  # an internal note's file

    client.force_login(customer)
    assert client.get(f"/account/support/attachments/{public.pk}/").status_code == 404
    client.logout()
    assert client.get(f"/account/support/attachments/{public.pk}/").status_code == 302

    client.force_login(agent)
    assert client.get(f"/account/support/attachments/{private.pk}/").status_code == 200


def test_a_missing_file_is_a_404_not_a_crash(client, owner, client_obj, technical, private_media):
    ticket = open_ticket(owner, client_obj, technical, files=[upload("gone.png")])
    attachment = TicketAttachment.objects.get(message__ticket=ticket)
    (private_media / attachment.file.name).unlink()
    client.force_login(owner)
    assert client.get(f"/account/support/attachments/{attachment.pk}/").status_code == 404


def test_html_named_uploads_cannot_become_a_page(client, owner, client_obj, technical):
    """Even a file that passed validation is only ever served as a download with a locked-down policy."""
    ticket = open_ticket(owner, client_obj, technical, files=[upload("notes.txt", b"<script>alert(1)</script>")])
    attachment = TicketAttachment.objects.get(message__ticket=ticket)
    client.force_login(owner)
    response = client.get(f"/account/support/attachments/{attachment.pk}/")
    assert response["Content-Type"] == "text/plain" and "attachment" in response["Content-Disposition"]
    assert "sandbox" in response["Content-Security-Policy"]


# --- Staff pages ----------------------------------------------------------------------------------------------------

def test_the_overview_and_ticket_list(client, owner, client_obj, technical, agent):
    a = open_ticket(owner, client_obj, technical, subject="Alpha problem")
    b = open_ticket(owner, client_obj, technical, subject="Beta problem")
    services.assign(agent, a, agent)
    services.set_priority(agent, b, "urgent")
    client.force_login(agent)
    overview = client.get("/staff/support/").content.decode()
    assert "Open tickets" in overview and "Alpha problem" in overview or a.reference in overview
    assert b.reference in overview and "Longest wait" in overview
    listing = lambda qs: client.get(f"/staff/support/tickets/?{qs}").content.decode()  # noqa: E731
    assert a.reference in listing("assigned=me") and b.reference not in listing("assigned=me")
    assert b.reference in listing("assigned=none") and a.reference not in listing("assigned=none")
    assert b.reference in listing("priority=urgent") and a.reference not in listing("priority=urgent")
    assert a.reference in listing("q=alpha") and b.reference not in listing("q=alpha")
    assert a.reference in listing("status=active") and "No tickets match" in listing("status=closed")
    assert "No tickets match" in listing("department=" + str(Department.objects.get(slug="billing").pk))


def test_staff_reply_notes_and_canned_replies(client, owner, client_obj, technical, agent):
    from apps.support.models import CannedReply

    ticket = open_ticket(owner, client_obj, technical)
    services.save_canned_reply(agent, None, title="Hello", body="Hi {client}, it is {agent} on {ticket}.")
    client.force_login(agent)
    page = client.get(f"/staff/support/tickets/{ticket.pk}/").content
    assert b"Predefined reply" in page and b"Hello" in page and b'name="internal"' in page
    canned = CannedReply.objects.get()
    inserted = client.post(f"/staff/support/tickets/{ticket.pk}/", {"canned": canned.pk, "insert": "1", "body": "Start:"})
    assert f"Hi Ada, it is".encode() in inserted.content and ticket.reference.encode() in inserted.content
    assert Ticket.objects.get().messages.count() == 1  # inserting only fills the box; nothing was sent

    mail.outbox.clear()
    client.post(f"/staff/support/tickets/{ticket.pk}/", {"body": "Real answer.", "set_status": "pending", "send": "1"})
    assert reload(ticket).status == TicketStatus.PENDING and len(mail.outbox) == 1
    client.post(f"/staff/support/tickets/{ticket.pk}/", {"body": "Careful with this one.", "internal": "on", "send": "1"})
    assert reload(ticket).status == TicketStatus.PENDING and len(mail.outbox) == 1  # a note sends nothing
    empty = client.post(f"/staff/support/tickets/{ticket.pk}/", {"body": " ", "send": "1"})
    assert b"Enter a reply" in empty.content


def test_staff_actions_from_the_ticket_page(client, owner, client_obj, technical, agent, manager):
    ticket = open_ticket(owner, client_obj, technical)
    client.force_login(agent)
    client.post(f"/staff/support/tickets/{ticket.pk}/take/")
    assert reload(ticket).assigned_to == agent
    client.post(f"/staff/support/tickets/{ticket.pk}/assign/", {"agent": str(manager.pk)})
    assert reload(ticket).assigned_to == manager
    client.post(f"/staff/support/tickets/{ticket.pk}/assign/", {"agent": ""})
    assert reload(ticket).assigned_to is None
    client.post(f"/staff/support/tickets/{ticket.pk}/priority/", {"priority": "high"})
    client.post(f"/staff/support/tickets/{ticket.pk}/status/", {"status": "resolved"})
    client.post(f"/staff/support/tickets/{ticket.pk}/department/", {"department": Department.objects.get(slug="billing").pk})
    ticket = reload(ticket)
    assert (ticket.priority, ticket.status, ticket.department.slug) == ("high", "resolved", "billing")
    client.post(f"/staff/support/tickets/{ticket.pk}/status/", {"status": "closed"})
    bad = client.post(f"/staff/support/tickets/{ticket.pk}/status/", {"status": "resolved"}, follow=True)
    assert b"cannot become" in bad.content and reload(ticket).status == TicketStatus.CLOSED


def test_view_only_staff_cannot_act(client, owner, client_obj, technical, staff):
    from django.contrib.auth.models import Permission

    ticket = open_ticket(owner, client_obj, technical)
    viewer = staff(Role.CUSTOMER)
    viewer.user_permissions.add(Permission.objects.get(codename="view_support", content_type__app_label="accounts"))
    client.force_login(type(viewer).objects.get(pk=viewer.pk))
    page = client.get(f"/staff/support/tickets/{ticket.pk}/").content
    assert ticket.subject.encode() in page and b"Send" not in page and b"Assign to me" not in page
    for action in ("take", "assign", "priority", "status", "department"):
        assert client.post(f"/staff/support/tickets/{ticket.pk}/{action}/").status_code == 403
    assert client.post(f"/staff/support/tickets/{ticket.pk}/", {"body": "x", "send": "1"}).status_code == 403
    assert client.get("/staff/support/tickets/new/").status_code == 403


def test_staff_open_a_ticket_for_a_client(client, owner, client_obj, technical, agent):
    client.force_login(agent)
    picker = client.get("/staff/support/tickets/new/?q=acme")
    assert client_obj.email.encode() in picker.content and b"tickets/new/?client=" in picker.content
    form = client.get(f"/staff/support/tickets/new/?client={client_obj.pk}")
    assert b"Urgent" in form.content  # staff can pick any priority
    response = client.post(f"/staff/support/tickets/new/?client={client_obj.pk}", {
        "department": technical.pk, "subject": "Called in", "priority": "urgent", "body": "Client phoned."})
    ticket = Ticket.objects.get()
    assert response["Location"] == f"/staff/support/tickets/{ticket.pk}/" and ticket.priority == TicketPriority.URGENT
    assert client.get("/staff/support/tickets/new/?client=99999").status_code == 404


def test_departments_replies_and_kb_management_pages(client, agent, manager):
    client.force_login(manager)
    client.post("/staff/support/departments/save/", {"name": "VIP", "sort_order": 9, "is_active": "on",
                                                    "default_assignee": str(agent.pk)})
    vip = Department.objects.get(name="VIP")
    assert vip.default_assignee == agent and b"VIP" in client.get("/staff/support/departments/").content
    client.post("/staff/support/departments/save/", {"id": vip.pk, "name": "VIP Care", "sort_order": 9})
    assert reload(vip).name == "VIP Care" and reload(vip).is_active is False
    assert b"Edit VIP Care" in client.get(f"/staff/support/departments/?edit={vip.pk}").content

    client.post("/staff/support/replies/save/", {"title": "Thanks", "body": "Thanks {client}", "sort_order": 0, "is_active": "on"})
    assert b"Thanks" in client.get("/staff/support/replies/").content

    hosting = KBCategory.objects.get(slug="hosting")
    client.post("/staff/support/kb/articles/save/", {"category": hosting.pk, "title": "Getting online", "body": "Step one.\n\nStep two.",
                                                    "sort_order": 0, "is_published": "on"})
    client.post("/staff/support/kb/categories/save/", {"name": "Security", "sort_order": 9, "is_active": "on"})
    assert KBCategory.objects.filter(name="Security").exists()
    assert b"Getting online" in client.get("/staff/support/kb/").content
    article = kb.public_articles().get()
    client.post(f"/staff/support/kb/articles/{article.pk}/delete/")
    assert not kb.public_articles().exists()

    client.force_login(agent)  # agents manage support too
    assert client.post("/staff/support/replies/save/", {"title": "T2", "body": "b", "sort_order": 0}).status_code == 302


def test_the_client_profile_lists_tickets(client, owner, client_obj, technical, manager):
    ticket = open_ticket(owner, client_obj, technical, subject="Profile ticket")
    client.force_login(manager)
    page = client.get(f"/staff/clients/{client_obj.pk}/").content
    assert ticket.reference.encode() in page and b"Tickets" in page


# --- The help centre ------------------------------------------------------------------------------------------------------

def test_the_help_centre_shows_only_published_articles_and_escapes_them(client, agent):
    hosting = KBCategory.objects.get(slug="hosting")
    article = kb.save_article(agent, None, category=hosting, title="Shared hosting <b>basics</b>",
                    body="First paragraph.\n\n<script>alert(1)</script>\nSecond line.", is_published=True)
    kb.save_article(agent, None, category=hosting, title="Hidden draft", body="draft", is_published=False)
    index = client.get("/help/").content
    assert b"/help/hosting/" in index and b"/help/domains/" not in index  # empty categories are hidden
    category = client.get("/help/hosting/").content
    assert b"basics" in category and b"Hidden draft" not in category
    page = client.get(f"/help/hosting/{article.slug}/").content
    assert b"First paragraph." in page and b"<script>" not in page and b"&lt;script&gt;" in page
    assert client.get("/help/hosting/hidden-draft/").status_code == 404
    assert client.get("/help/nonexistent/").status_code == 404
    found = client.get("/help/?q=paragraph").content
    assert b"1 result" in found and b"basics" in found
    assert b"0 results" in client.get("/help/?q=zzzz").content


# --- API -----------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", [("get", "/api/v1/tickets/"), ("post", "/api/v1/tickets/"),
                                         ("get", "/api/v1/departments/"), ("get", "/api/v1/canned-replies/")])
def test_api_authentication(api, method, path):
    assert getattr(api, method)(path).status_code == 401


def test_create_list_and_reply_over_the_api(api, owner, client_obj, agent):
    api.force_authenticate(owner)
    created = api.post("/api/v1/tickets/", {"department": "technical", "subject": "API ticket", "body": "Hello",
                                            "priority": "high"}, format="json")
    assert created.status_code == 201, created.content
    body = created.json()
    assert body["reference"].startswith("T") and body["status"] == "open" and body["assigned_to"] is None
    assert [m["body"] for m in body["messages"]] == ["Hello"]

    reply = api.post(f"/api/v1/tickets/{body['id']}/reply/", {"body": "More info"}, format="json")
    assert reply.status_code == 201 and reply.json()["kind"] == "customer"
    assert api.get(f"/api/v1/tickets/{body['id']}/").json()["status"] == "customer_reply"
    assert api.get("/api/v1/tickets/?status=active").json()["count"] == 1
    assert api.get("/api/v1/tickets/?status=closed").json()["count"] == 0
    assert api.post(f"/api/v1/tickets/{body['id']}/close/").json()["status"] == "closed"


def test_uploads_over_the_api(api, owner, client_obj):
    api.force_authenticate(owner)
    created = api.post("/api/v1/tickets/", {"department": "technical", "subject": "With file", "body": "See attached",
                                            "files": [upload("shot.png")]}, format="multipart")
    assert created.status_code == 201, created.content
    attachment = created.json()["messages"][0]["attachments"][0]
    assert attachment["original_name"] == "shot.png" and attachment["content_type"] == "image/png"
    download = api.get(attachment["url"])
    assert download.status_code == 200 and "attachment" in download["Content-Disposition"]
    bad = api.post("/api/v1/tickets/", {"department": "technical", "subject": "Bad", "body": "x",
                                        "files": [upload("evil.exe", b"MZ0000")]}, format="multipart")
    assert bad.status_code == 400 and Ticket.objects.count() == 1


def test_customers_never_get_internal_notes_or_agent_identities_from_the_api(api, owner, agent, client_obj, technical):
    ticket = open_ticket(owner, client_obj, technical)
    services.assign(agent, ticket, agent)
    services.reply(agent, ticket, "INTERNAL-ONLY", internal=True, files=[upload("n.txt", b"internal file")])
    services.reply(agent, ticket, "Public reply")
    api.force_authenticate(owner)
    body = api.get(f"/api/v1/tickets/{ticket.pk}/").json()
    assert [m["body"] for m in body["messages"]] == ["It shows a 500 error.", "Public reply"]
    assert not any(m["is_internal"] for m in body["messages"])
    assert body["assigned_to"] == "support" and agent.email not in str(body)  # not even as a reply's author
    note = ticket.messages.get(is_internal=True).attachments.get()
    assert api.get(f"/api/v1/tickets/{ticket.pk}/attachments/{note.pk}/").status_code == 404

    api.force_authenticate(agent)
    staff_body = api.get(f"/api/v1/tickets/{ticket.pk}/").json()
    assert any(m["is_internal"] for m in staff_body["messages"]) and staff_body["assigned_to"] == agent.email
    assert api.get(f"/api/v1/tickets/{ticket.pk}/attachments/{note.pk}/").status_code == 200


def test_scoping_permissions_and_client_resolution(api, owner, stranger, customer, client_obj, other_client, technical, agent, manager):
    ticket = open_ticket(owner, client_obj, technical)
    api.force_authenticate(stranger)
    assert api.get(f"/api/v1/tickets/{ticket.pk}/").status_code == 404
    assert api.post(f"/api/v1/tickets/{ticket.pk}/reply/", {"body": "hi"}, format="json").status_code == 404
    assert api.post("/api/v1/tickets/", {"client": client_obj.pk, "department": "technical", "subject": "s", "body": "b"},
                    format="json").status_code == 404  # someone else's client looks missing
    api.force_authenticate(owner)
    for action, payload in (("status", {"status": "resolved"}), ("assign", {"agent": agent.pk}),
                            ("priority", {"priority": "urgent"}), ("department", {"department": "billing"})):
        assert api.post(f"/api/v1/tickets/{ticket.pk}/{action}/", payload, format="json").status_code == 403
    assert api.post(f"/api/v1/tickets/{ticket.pk}/reply/", {"body": "x", "internal": True}, format="json").status_code == 403
    api.force_authenticate(customer)  # belongs to no client
    assert api.post("/api/v1/tickets/", {"department": "technical", "subject": "s", "body": "b"}, format="json"
                    ).json()["error"]["code"] == "client_required"

    api.force_authenticate(agent)
    assert api.post("/api/v1/tickets/", {"department": "technical", "subject": "s", "body": "b"}, format="json"
                    ).json()["error"]["code"] == "client_required"  # staff must name the client
    assert api.post(f"/api/v1/tickets/{ticket.pk}/status/", {"status": "resolved"}, format="json").json()["status"] == "resolved"
    assert api.post(f"/api/v1/tickets/{ticket.pk}/assign/", {"agent": manager.pk}, format="json").json()["assigned_to"] == manager.email
    assert api.post(f"/api/v1/tickets/{ticket.pk}/assign/", {"agent": owner.pk}, format="json").json()["error"]["code"] == "assignee_invalid"
    assert api.post(f"/api/v1/tickets/{ticket.pk}/assign/", {"agent": None}, format="json").json()["assigned_to"] is None
    assert api.post(f"/api/v1/tickets/{ticket.pk}/priority/", {"priority": "urgent"}, format="json").json()["priority"] == "urgent"
    assert api.post(f"/api/v1/tickets/{ticket.pk}/department/", {"department": "billing"}, format="json").json()["department"] == "billing"
    assert api.post(f"/api/v1/tickets/{ticket.pk}/status/", {"status": "closed"}, format="json").status_code == 200
    illegal = api.post(f"/api/v1/tickets/{ticket.pk}/status/", {"status": "pending"}, format="json")
    assert illegal.status_code == 409 and illegal.json()["error"]["code"] == "invalid_transition"
    assert api.get(f"/api/v1/tickets/?client={client_obj.pk}&assigned=none&priority=urgent&department=billing").json()["count"] == 1
    on_behalf = api.post("/api/v1/tickets/", {"client": client_obj.pk, "department": "technical", "subject": "By phone",
                                              "body": "Client called", "priority": "urgent"}, format="json")
    assert on_behalf.status_code == 201 and on_behalf.json()["messages"][0]["kind"] == "staff"


def test_departments_and_canned_replies_over_the_api(api, owner, agent, manager):
    api.force_authenticate(owner)
    assert {d["slug"] for d in api.get("/api/v1/departments/").json()} >= {"technical", "billing"}
    assert api.post("/api/v1/departments/", {"name": "X"}, format="json").status_code == 403
    assert api.get("/api/v1/canned-replies/").status_code == 403
    api.force_authenticate(agent)
    made = api.post("/api/v1/departments/", {"name": "Abuse", "sort_order": 5, "default_assignee_id": agent.pk}, format="json")
    assert made.status_code == 201 and made.json()["slug"] == "abuse" and made.json()["default_assignee"] == agent.email
    hidden = api.put(f"/api/v1/departments/{made.json()['id']}/", {"name": "Abuse", "is_active": False}, format="json")
    assert hidden.status_code == 200 and hidden.json()["is_active"] is False
    api.force_authenticate(owner)
    assert "abuse" not in {d["slug"] for d in api.get("/api/v1/departments/").json()}  # customers see active only
    api.force_authenticate(agent)
    reply = api.post("/api/v1/canned-replies/", {"title": "Hi", "body": "Hello {client}"}, format="json")
    assert reply.status_code == 201 and api.get("/api/v1/canned-replies/").json()[0]["title"] == "Hi"
    assert api.put(f"/api/v1/canned-replies/{reply.json()['id']}/", {"title": "Hi2", "body": "b"}, format="json").json()["title"] == "Hi2"


def test_the_knowledgebase_api(api, owner, agent):
    assert api.get("/api/v1/kb/articles/").json()["count"] == 0
    assert api.post("/api/v1/kb/articles/", {"category": "hosting", "title": "x", "body": "y"}, format="json").status_code == 401
    api.force_authenticate(owner)
    assert api.post("/api/v1/kb/articles/", {"category": "hosting", "title": "x", "body": "y"}, format="json").status_code == 403
    api.force_authenticate(agent)
    made = api.post("/api/v1/kb/articles/", {"category": "hosting", "title": "Draft one", "body": "text"}, format="json")
    assert made.status_code == 201 and made.json()["is_published"] is False
    api.force_authenticate(None)
    assert api.get("/api/v1/kb/articles/").json()["count"] == 0  # drafts are invisible to the public
    api.force_authenticate(agent)
    api.put(f"/api/v1/kb/articles/{made.json()['id']}/", {"category": "hosting", "title": "Draft one", "body": "text",
                                                       "is_published": True}, format="json")
    api.force_authenticate(None)
    listing = api.get("/api/v1/kb/articles/?category=hosting&q=text").json()
    assert listing["count"] == 1 and listing["results"][0]["slug"] == "draft-one"
    assert [c["slug"] for c in api.get("/api/v1/kb/categories/").json()] == ["hosting"]
    api.force_authenticate(agent)
    assert api.delete(f"/api/v1/kb/articles/{made.json()['id']}/").status_code == 204
    made_cat = api.post("/api/v1/kb/categories/", {"name": "Security"}, format="json")
    assert made_cat.status_code == 201 and made_cat.json()["slug"] == "security"
