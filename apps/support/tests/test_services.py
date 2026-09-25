"""Tickets: opening, replying, internal notes, status, assignment, uploads, visibility and the knowledgebase."""
from datetime import timedelta

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.core.exceptions import ServiceError
from apps.notifications.models import Notification
from apps.support import kb, lifecycle, services, uploads
from apps.support.models import (CannedReply, Department, KBArticle, KBCategory, MessageKind, Ticket,
                                 TicketAttachment, TicketPriority, TicketStatus)

from .conftest import JPG, PDF, PNG, upload

pytestmark = pytest.mark.django_db


def open_ticket(owner, client_obj, technical, **kwargs):
    kwargs.setdefault("subject", "My site is down")
    kwargs.setdefault("body", "It shows a 500 error since this morning.")
    return services.open_ticket(owner, client_obj, department=technical, **kwargs)


def reload(obj):
    obj.refresh_from_db()
    return obj


# --- Opening ---------------------------------------------------------------------------------------------

def test_a_customer_opens_a_ticket(owner, client_obj, technical):
    mail.outbox.clear()
    ticket = open_ticket(owner, client_obj, technical, priority="high")
    assert ticket.reference == f"T{ticket.pk:06d}" and ticket.status == TicketStatus.OPEN
    assert ticket.priority == "high" and ticket.opened_by == owner and ticket.assigned_to is None
    first = ticket.messages.get()
    assert first.kind == MessageKind.CUSTOMER and first.body.startswith("It shows") and not first.is_internal
    assert ticket.last_customer_reply_at is not None
    assert AuditEvent.objects.filter(action="ticket.opened", target_id=str(ticket.pk)).exists()
    assert len(mail.outbox) == 1 and ticket.reference in mail.outbox[0].subject
    assert mail.outbox[0].to == [owner.email]


def test_a_department_default_assignee_gets_new_tickets_and_a_notification(owner, client_obj, technical, agent):
    services.save_department(agent, technical, name=technical.name, default_assignee=agent)
    ticket = open_ticket(owner, client_obj, technical)
    assert ticket.assigned_to == agent
    assert Notification.objects.filter(user=agent, event="ticket.assigned").exists()


@pytest.mark.parametrize("kwargs", [
    {"subject": "  "}, {"body": ""}, {"subject": "x" * 201}, {"body": "x" * 20001},
    {"priority": "urgent"},  # only staff can mark a ticket urgent
    {"priority": "bogus"},
])
def test_invalid_tickets_are_refused_and_nothing_is_written(owner, client_obj, technical, kwargs):
    with pytest.raises((ValidationError, ServiceError)):
        open_ticket(owner, client_obj, technical, **kwargs)
    assert not Ticket.objects.exists()


def test_the_department_must_be_active(owner, client_obj, technical, agent):
    services.save_department(agent, technical, name=technical.name, is_active=False)
    with pytest.raises(ServiceError) as exc:
        open_ticket(owner, client_obj, technical)
    assert exc.value.code == "department_invalid"
    with pytest.raises(ServiceError):
        services.open_ticket(owner, client_obj, department=None, subject="x", body="y")


def test_only_contacts_and_staff_can_open_a_ticket_for_a_client(customer, owner, client_obj, technical, agent):
    with pytest.raises(ServiceError) as exc:
        open_ticket(customer, client_obj, technical)  # someone else's client
    assert exc.value.code == "permission_denied"
    on_behalf = open_ticket(agent, client_obj, technical)
    assert on_behalf.messages.get().kind == MessageKind.STAFF
    assert AuditEvent.objects.get(action="ticket.opened", target_id=str(on_behalf.pk)).metadata["on_behalf"] is True


def test_a_ticket_opened_by_staff_is_emailed_to_the_client_not_the_agent(owner, client_obj, technical, agent):
    mail.outbox.clear()
    ticket = open_ticket(agent, client_obj, technical)
    assert mail.outbox[0].to == [client_obj.email] and agent.email not in mail.outbox[0].to
    mail.outbox.clear()
    services.reply(agent, ticket, "We are looking into it.")
    assert mail.outbox[0].to == [client_obj.email]
    assert not Notification.objects.filter(user=agent, event="ticket.reply").exists()


def test_related_service_and_domain_must_belong_to_the_client(owner, client_obj, other_client, technical, manager):
    from apps.domains.models import Domain
    from apps.hosting.models import HostingAccount
    from apps.products import services as product_services
    from apps.products.models import ProductType

    product = product_services.create_product(manager, {"name": "P", "type": ProductType.SHARED_HOSTING,
                                                        "whm_package_name": "p"})
    mine = HostingAccount.objects.create(client=client_obj, product=product, domain="mine.com", username="mine")
    theirs = HostingAccount.objects.create(client=other_client, product=product, domain="theirs.com", username="theirs")
    their_domain = Domain.objects.create(client=other_client, name="theirs.com")
    my_domain = Domain.objects.create(client=client_obj, name="mine.com")
    ticket = open_ticket(owner, client_obj, technical, hosting_account=mine, domain=my_domain)
    assert ticket.hosting_account == mine and ticket.domain == my_domain
    for kwargs in ({"hosting_account": theirs}, {"domain": their_domain}):
        with pytest.raises(ServiceError) as exc:
            open_ticket(owner, client_obj, technical, **kwargs)
        assert exc.value.code == "invalid_related"


def test_customers_are_rate_limited_staff_are_not(owner, client_obj, technical, agent):
    for _ in range(services.TICKETS_PER_HOUR):
        open_ticket(owner, client_obj, technical)
    with pytest.raises(ServiceError) as exc:
        open_ticket(owner, client_obj, technical)
    assert exc.value.code == "rate_limited" and exc.value.status_code == 429
    assert open_ticket(agent, client_obj, technical)


# --- Attachments -------------------------------------------------------------------------------------------

def test_valid_attachments_are_stored_privately_under_random_names(owner, client_obj, technical, private_media, settings):
    ticket = open_ticket(owner, client_obj, technical, files=[upload("Screen Shot.png"), upload("report.pdf", PDF)])
    attachments = list(TicketAttachment.objects.filter(message__ticket=ticket))
    assert [a.original_name for a in attachments] == ["Screen Shot.png", "report.pdf"]
    assert [a.content_type for a in attachments] == ["image/png", "application/pdf"]
    for attachment in attachments:
        path = private_media / attachment.file.name
        assert path.exists() and str(path).startswith(str(private_media))
        assert "Screen" not in attachment.file.name  # the uploader's filename is display text only
        assert not str(path).startswith(str(settings.MEDIA_ROOT))  # never under the publicly served directory


@pytest.mark.parametrize("name,content,message", [
    ("evil.exe", b"MZ" + b"0" * 20, "not an allowed file type"),
    ("page.html", b"<script>alert(1)</script>", "not an allowed file type"),
    ("image.svg", b"<svg onload=alert(1)>", "not an allowed file type"),
    ("noext", PNG, "not an allowed file type"),
    ("fake.png", b"<html>not a png</html>", "does not look like a valid PNG"),
    ("fake.pdf", PNG, "does not look like a valid PDF"),
    ("fake.jpg", PDF, "does not look like a valid JPG"),
    ("bin.txt", b"text\x00with a null", "does not look like a valid TXT"),
    ("empty.png", b"", "is empty"),
])
def test_bad_uploads_are_refused_before_anything_is_written(owner, client_obj, technical, name, content, message,
                                                            private_media):
    with pytest.raises(ValidationError) as exc:
        open_ticket(owner, client_obj, technical, files=[upload("good.png"), upload(name, content)])
    assert message in exc.value.messages[0]
    assert not Ticket.objects.exists() and not TicketAttachment.objects.exists()
    assert not private_media.exists() or not any(private_media.rglob("*.*"))  # not even the good file was kept


def test_size_and_count_limits(owner, client_obj, technical, settings):
    settings.SUPPORT_MAX_ATTACHMENT_MB = 1
    with pytest.raises(ValidationError) as exc:
        open_ticket(owner, client_obj, technical, files=[upload("big.png", PNG + b"0" * (1024 * 1024))])
    assert "larger than 1 MB" in exc.value.messages[0]
    with pytest.raises(ValidationError) as exc:
        open_ticket(owner, client_obj, technical, files=[upload(f"f{i}.png") for i in range(uploads.MAX_FILES + 1)])
    assert "at most" in exc.value.messages[0]


@pytest.mark.parametrize("raw,clean", [
    ("../../etc/passwd.txt", "passwd.txt"), ("C:\\Users\\x\\note.txt", "note.txt"), ("a<b>|c?.png", "a_b__c_.png"),
    ("  .hidden.png ", "hidden.png"), ("x" * 300 + ".png", ("x" * 96) + ".png"),
])
def test_filenames_are_cleaned(raw, clean):
    assert uploads.clean_name(raw) == clean


def test_the_content_type_comes_from_the_extension_not_the_browser(owner, client_obj, technical):
    file = upload("photo.jpg", JPG)
    file.content_type = "text/html"
    ticket = open_ticket(owner, client_obj, technical, files=[file])
    assert TicketAttachment.objects.get(message__ticket=ticket).content_type == "image/jpeg"


# --- Replying ------------------------------------------------------------------------------------------------

def test_customer_and_agent_replies_move_the_ticket_between_the_states(owner, client_obj, technical, agent):
    ticket = open_ticket(owner, client_obj, technical)
    services.assign(agent, ticket, agent)
    mail.outbox.clear()
    services.reply(agent, ticket, "Have you tried clearing the cache?")
    ticket = reload(ticket)
    assert ticket.status == TicketStatus.AGENT_REPLY and ticket.last_staff_reply_at
    assert len(mail.outbox) == 1 and "clearing the cache" in mail.outbox[0].body and mail.outbox[0].to == [owner.email]
    assert Notification.objects.filter(user=owner, event="ticket.reply").exists()

    services.reply(owner, ticket, "Yes, still broken.")
    ticket = reload(ticket)
    assert ticket.status == TicketStatus.CUSTOMER_REPLY and ticket.awaiting_agent
    assert Notification.objects.filter(user=agent, event="ticket.customer_reply").exists()
    moves = [(e.metadata["from"], e.metadata["to"]) for e in AuditEvent.objects.filter(
        action="ticket.status_changed", target_id=str(ticket.pk)).order_by("id")]
    assert moves == [("open", "agent_reply"), ("agent_reply", "customer_reply")]


def test_an_agent_can_reply_and_set_the_next_status_in_one_step(owner, client_obj, technical, agent):
    ticket = open_ticket(owner, client_obj, technical)
    services.reply(agent, ticket, "Fixed it.", set_status=TicketStatus.RESOLVED)
    ticket = reload(ticket)
    assert ticket.status == TicketStatus.RESOLVED and ticket.resolved_at
    services.reply(owner, ticket, "Actually it broke again.")
    ticket = reload(ticket)
    assert ticket.status == TicketStatus.CUSTOMER_REPLY and ticket.resolved_at is None  # a reply reopens it


def test_internal_notes_are_never_visible_or_emailed_to_the_customer(owner, client_obj, technical, agent, customer):
    ticket = open_ticket(owner, client_obj, technical)
    mail.outbox.clear()
    note = services.reply(agent, ticket, "Customer is on a legacy plan, be careful.", internal=True,
                          files=[upload("notes.txt", b"secret detail")])
    ticket = reload(ticket)
    assert note.is_internal and ticket.status == TicketStatus.OPEN  # a note does not move the ticket
    assert ticket.last_staff_reply_at is None and mail.outbox == []
    assert not Notification.objects.filter(user=owner).filter(event__startswith="ticket.note").exists()
    assert [m.body for m in services.messages_for(owner, ticket)] == ["It shows a 500 error since this morning."]
    assert note in services.messages_for(agent, ticket)
    attachment = TicketAttachment.objects.get(message=note)
    assert services.can_view_attachment(agent, attachment)
    assert not services.can_view_attachment(owner, attachment) and not services.can_view_attachment(customer, attachment)


def test_customers_cannot_write_notes_or_set_statuses(owner, client_obj, technical):
    ticket = open_ticket(owner, client_obj, technical)
    with pytest.raises(ServiceError) as exc:
        services.reply(owner, ticket, "sneaky", internal=True)
    assert exc.value.code == "permission_denied"
    with pytest.raises(ServiceError):
        services.reply(owner, ticket, "sneaky", set_status=TicketStatus.RESOLVED)
    with pytest.raises(ServiceError):
        services.set_status(owner, ticket, TicketStatus.RESOLVED)
    assert ticket.messages.count() == 1


def test_a_stranger_cannot_reply(customer, owner, client_obj, technical):
    ticket = open_ticket(owner, client_obj, technical)
    with pytest.raises(ServiceError) as exc:
        services.reply(customer, ticket, "hi")
    assert exc.value.code == "permission_denied"


def test_closed_tickets_can_be_answered_for_a_week_then_a_new_ticket_is_needed(owner, client_obj, technical, agent):
    ticket = open_ticket(owner, client_obj, technical)
    services.set_status(agent, ticket, TicketStatus.CLOSED)
    with pytest.raises(ServiceError) as exc:
        services.reply(agent, ticket, "one more thing")  # staff reopen first
    assert exc.value.code == "ticket_closed"
    services.reply(owner, ticket, "Wait, one more question")  # within the window
    assert reload(ticket).status == TicketStatus.CUSTOMER_REPLY and reload(ticket).closed_at is None

    services.set_status(agent, ticket, TicketStatus.CLOSED)
    Ticket.objects.filter(pk=ticket.pk).update(closed_at=timezone.now() - timedelta(days=8))
    with pytest.raises(ServiceError) as exc:
        services.reply(owner, reload(ticket), "hello?")
    assert exc.value.code == "ticket_closed"


# --- Status ------------------------------------------------------------------------------------------------------

def test_staff_set_any_legal_status_and_it_is_audited(owner, client_obj, technical, agent):
    ticket = open_ticket(owner, client_obj, technical)
    for new in (TicketStatus.PENDING, TicketStatus.RESOLVED, TicketStatus.CLOSED, TicketStatus.OPEN):
        ticket = services.set_status(agent, ticket, new, reason="test")
        assert ticket.status == new
    assert ticket.closed_at is None and ticket.resolved_at is None  # reopening clears the timestamps
    assert AuditEvent.objects.filter(action="ticket.status_changed", target_id=str(ticket.pk)).count() == 4


@pytest.mark.parametrize("new", [TicketStatus.RESOLVED, TicketStatus.PENDING, TicketStatus.AGENT_REPLY,
                                 TicketStatus.CUSTOMER_REPLY])
def test_a_closed_ticket_can_only_be_reopened(owner, client_obj, technical, agent, new):
    ticket = open_ticket(owner, client_obj, technical)
    services.set_status(agent, ticket, TicketStatus.CLOSED)
    with pytest.raises(ServiceError) as exc:
        services.set_status(agent, ticket, new)
    assert exc.value.code == "invalid_transition" and exc.value.status_code == 409
    assert reload(ticket).status == TicketStatus.CLOSED


def test_the_table_is_complete():
    assert not lifecycle.can_transition("open", "open") and not lifecycle.can_transition("open", "bogus")
    assert lifecycle.can_transition("resolved", "open") and lifecycle.can_transition("closed", "open")
    assert not lifecycle.can_transition("closed", "pending")


def test_customers_can_close_only_their_own_ticket(customer, owner, client_obj, technical):
    ticket = open_ticket(owner, client_obj, technical)
    with pytest.raises(ServiceError):
        services.set_status(customer, ticket, TicketStatus.CLOSED)
    assert services.set_status(owner, ticket, TicketStatus.CLOSED).status == TicketStatus.CLOSED


def test_resolved_tickets_close_themselves_after_a_week(owner, client_obj, technical, agent):
    fresh = open_ticket(owner, client_obj, technical)
    old = open_ticket(owner, client_obj, technical)
    services.set_status(agent, fresh, TicketStatus.RESOLVED)
    services.set_status(agent, old, TicketStatus.RESOLVED)
    Ticket.objects.filter(pk=old.pk).update(resolved_at=timezone.now() - timedelta(days=8))
    assert services.auto_close_resolved() == 1
    assert reload(old).status == TicketStatus.CLOSED and reload(fresh).status == TicketStatus.RESOLVED
    assert AuditEvent.objects.filter(action="ticket.auto_closed", target_id=str(old.pk)).exists()
    assert services.auto_close_resolved() == 0


# --- Assignment, priority, department ----------------------------------------------------------------------------

def test_assignment_rules(owner, client_obj, technical, agent, manager, customer):
    ticket = open_ticket(owner, client_obj, technical)
    assert services.assign(agent, ticket, manager).assigned_to == manager
    assert Notification.objects.filter(user=manager, event="ticket.assigned").exists()
    assert services.assign(agent, ticket, manager).assigned_to == manager  # idempotent
    assert services.assign(agent, ticket, None).assigned_to is None
    for bad in (customer, owner):  # a customer cannot handle tickets
        with pytest.raises(ServiceError) as exc:
            services.assign(agent, ticket, bad)
        assert exc.value.code == "assignee_invalid"
    with pytest.raises(ServiceError):
        services.assign(owner, ticket, agent)
    assert agent in services.assignable_agents() and owner not in services.assignable_agents()


def test_priority_and_department_changes(owner, client_obj, technical, agent):
    ticket = open_ticket(owner, client_obj, technical)
    assert services.set_priority(agent, ticket, "urgent").priority == "urgent"
    billing = Department.objects.get(slug="billing")
    assert services.set_department(agent, ticket, billing).department == billing
    for call in (lambda: services.set_priority(owner, ticket, "low"), lambda: services.set_department(owner, ticket, billing)):
        with pytest.raises(ServiceError):
            call()
    with pytest.raises(ServiceError):
        services.set_priority(agent, ticket, "bogus")
    assert AuditEvent.objects.filter(action="ticket.priority_changed").count() == 1


# --- Visibility, search, overview -------------------------------------------------------------------------------

def test_who_sees_which_tickets(owner, stranger, customer, client_obj, other_client, technical, agent):
    mine = open_ticket(owner, client_obj, technical)
    theirs = open_ticket(stranger, other_client, technical)
    assert list(services.visible_tickets_for_user(owner)) == [mine]
    assert list(services.visible_tickets_for_user(stranger)) == [theirs]
    assert list(services.visible_tickets_for_user(customer)) == []
    assert set(services.visible_tickets_for_user(agent)) == {mine, theirs}
    assert list(services.visible_tickets_for_user(None)) == []


def test_search(owner, client_obj, technical):
    a = open_ticket(owner, client_obj, technical, subject="Email not working")
    open_ticket(owner, client_obj, technical, subject="Billing question")
    found = lambda term: set(services.search_tickets(Ticket.objects.all(), term))  # noqa: E731
    assert found("email") == {a} and found(f"T{a.pk:06d}") == {a} and found(str(a.pk)) == {a}
    assert found("acme") == set(Ticket.objects.all()) and found("") == set(Ticket.objects.all())


def test_the_overview_counts(owner, client_obj, technical, agent):
    a = open_ticket(owner, client_obj, technical)
    b = open_ticket(owner, client_obj, technical)
    services.assign(agent, a, agent)
    services.set_priority(agent, b, "urgent")
    services.set_status(agent, open_ticket(owner, client_obj, technical), TicketStatus.RESOLVED)
    data = services.overview(agent)
    assert data["counts"] == {"active": 2, "awaiting_agent": 2, "unassigned": 1, "mine": 1, "urgent": 1,
                              "resolved_week": 1}
    assert data["oldest_waiting"] == a and data["by_department"][0]["total"] == 2
    assert [t.pk for t in data["needs_attention"]][0] == b.pk  # urgent first


# --- Departments and predefined replies --------------------------------------------------------------------------

def test_departments_are_managed_by_staff_with_unique_slugs(agent, owner):
    one = services.save_department(agent, None, name="VIP Support")
    two = services.save_department(agent, None, name="VIP  Support!")
    assert (one.slug, two.slug) == ("vip-support", "vip-support-2") and one.is_active
    with pytest.raises(ServiceError):
        services.save_department(owner, None, name="Nope")
    with pytest.raises(ServiceError):
        services.save_department(agent, one, name="X", default_assignee=owner)  # not an agent
    with pytest.raises(ValidationError):
        services.save_department(agent, None, name="")


def test_predefined_replies_fill_in_names_and_respect_departments(owner, client_obj, technical, agent):
    ticket = open_ticket(owner, client_obj, technical)
    everywhere = services.save_canned_reply(agent, None, title="Greeting", body="Hi {client}, this is {agent} about {ticket}.")
    billing_only = services.save_canned_reply(agent, None, title="Refund", body="x",
                                              department=Department.objects.get(slug="billing"))
    assert set(services.canned_replies_for(technical)) == {everywhere}
    assert set(services.canned_replies_for(Department.objects.get(slug="billing"))) == {everywhere, billing_only}
    text = services.render_canned(everywhere, ticket, agent)
    assert text == f"Hi Ada, this is Support team about {ticket.reference}."  # never the agent's email address
    with pytest.raises(ServiceError):
        services.save_canned_reply(owner, None, title="x", body="y")
    with pytest.raises(ValidationError):
        services.save_canned_reply(agent, None, title="", body="y")
    assert CannedReply.objects.count() == 2


# --- Knowledgebase -----------------------------------------------------------------------------------------------

def test_the_default_departments_and_categories_are_seeded():
    assert set(Department.objects.values_list("slug", flat=True)) >= {"technical", "billing", "sales", "general"}
    assert list(KBCategory.objects.values_list("name", flat=True)) == [
        "Getting Started", "Hosting", "Domains", "cPanel", "DNS", "Email", "WordPress", "Billing"]


def test_only_published_articles_in_active_categories_are_public(agent):
    hosting = KBCategory.objects.get(slug="hosting")
    live = kb.save_article(agent, None, category=hosting, title="What is shared hosting?", body="Plain text.",
                           is_published=True)
    kb.save_article(agent, None, category=hosting, title="Draft", body="secret", is_published=False)
    assert set(kb.public_articles()) == {live}
    assert [c.slug for c in kb.public_categories()] == ["hosting"]  # empty categories are hidden
    assert kb.public_categories()[0].article_count == 1
    kb.save_category(agent, hosting, name="Hosting", is_active=False)
    assert set(kb.public_articles()) == set() and list(kb.public_categories()) == []


def test_knowledgebase_search_and_slugs(agent):
    dns = KBCategory.objects.get(slug="dns")
    a = kb.save_article(agent, None, category=dns, title="Change nameservers", body="Log in and edit NS records.",
                        is_published=True)
    b = kb.save_article(agent, None, category=dns, title="Change nameservers", body="Second.", is_published=True)
    assert (a.slug, b.slug) == ("change-nameservers", "change-nameservers-2")
    assert set(kb.search_public("nameservers")) == {a, b} and set(kb.search_public("NS records")) == {a}
    assert list(kb.search_public("  ")) == [] and list(kb.search_public("zzz")) == []


def test_knowledgebase_management_needs_manage_support(owner, agent):
    category = KBCategory.objects.get(slug="email")
    with pytest.raises(ServiceError) as exc:
        kb.save_article(owner, None, category=category, title="x", body="y")
    assert exc.value.code == "permission_denied"
    article = kb.save_article(agent, None, category=category, title="Set up email", body="y")
    with pytest.raises(ServiceError):
        kb.delete_article(owner, article)
    kb.delete_article(agent, article)
    assert not KBArticle.objects.exists() and AuditEvent.objects.filter(action="kb_article.deleted").exists()
    with pytest.raises(ServiceError):
        kb.save_article(agent, None, category=category, title="Long", body="x" * (kb.MAX_BODY + 1))


def test_view_only_staff_can_read_but_not_act(staff, owner, client_obj, technical):
    ticket = open_ticket(owner, client_obj, technical)
    from django.contrib.auth.models import Permission

    viewer = staff(Role.CUSTOMER)
    viewer.user_permissions.add(Permission.objects.get(codename="view_support", content_type__app_label="accounts"))
    viewer = type(viewer).objects.get(pk=viewer.pk)
    assert list(services.visible_tickets_for_user(viewer)) == [ticket]
    assert not services.can_manage(viewer)
    with pytest.raises(ServiceError):
        services.reply(viewer, ticket, "hello")
    with pytest.raises(ServiceError):
        services.assign(viewer, ticket, None)


def test_the_priority_choices_have_the_documented_order():
    assert TicketPriority.values == ["low", "normal", "high", "urgent"]


# --- Storage safety ---------------------------------------------------------------------------------------------

def test_a_storage_failure_part_way_leaves_no_ticket_and_no_orphan_files(owner, client_obj, technical, private_media,
                                                                          monkeypatch):
    from django.db.models.fields.files import FieldFile

    original, calls = FieldFile.save, {"n": 0}

    def flaky(self, name, content, save=True):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return original(self, name, content, save)

    monkeypatch.setattr(FieldFile, "save", flaky)
    with pytest.raises(OSError):
        open_ticket(owner, client_obj, technical, files=[upload("one.png"), upload("two.png")])
    assert not Ticket.objects.exists() and not TicketAttachment.objects.exists()
    assert not private_media.exists() or not any(p.is_file() for p in private_media.rglob("*"))  # the first file was removed


def test_private_files_are_never_under_a_publicly_served_path(settings):
    from pathlib import Path

    from django.conf import settings as real

    private = Path(real.PRIVATE_MEDIA_ROOT).resolve()
    assert not str(private).startswith(str(Path(real.MEDIA_ROOT).resolve()))
    assert not str(private).startswith(str(Path(real.STATIC_ROOT).resolve()))
    nginx = (Path(real.BASE_DIR) / "deploy" / "nginx" / "wpanel.conf").read_text()
    assert "private_media" not in nginx  # nginx has no route to the attachments
    compose = (Path(real.BASE_DIR) / "docker-compose.yml").read_text()
    web_and_worker = compose.split("nginx:")[0]
    assert "private_media:/app/private_media" in web_and_worker
    assert "private_media" not in compose.split("nginx:")[1].split("volumes:\n  pgdata")[0]  # nginx does not mount it


def test_the_daily_celery_task_closes_stale_resolved_tickets(owner, client_obj, technical, agent):
    from apps.support import tasks

    ticket = open_ticket(owner, client_obj, technical)
    services.set_status(agent, ticket, TicketStatus.RESOLVED)
    Ticket.objects.filter(pk=ticket.pk).update(resolved_at=timezone.now() - timedelta(days=9))
    assert tasks.auto_close_resolved_tickets_task() == 1
    assert reload(ticket).status == TicketStatus.CLOSED
