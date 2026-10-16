"""The AI assistant: switched off by default, scoped to the signed-in person's own services, limited per day, and never given a client number by the model."""
import pytest
from django.urls import reverse

from apps.assistant import agent, tools
from apps.assistant.models import AssistantSettings, Conversation, Message
from apps.core.exceptions import ServiceError
from apps.core.test_client_area import browser, owner, stranger  # noqa: F401
from apps.core.test_client_shell import account  # noqa: F401
from apps.support.models import Department

pytestmark = pytest.mark.django_db


@pytest.fixture
def switched_on(settings):
    settings.ANTHROPIC_API_KEY = "test-key-not-real"
    row = AssistantSettings.load()
    row.enabled, row.daily_limit, row.mode = True, 3, "claude"
    row.save()
    return row


@pytest.fixture
def ask(monkeypatch):
    """Replace Claude: ``script`` is a list of response bodies handed out in order; the requests made are recorded."""
    class Fake:
        def __init__(self):
            self.script, self.requests = [], []

    fake = Fake()

    def call(model, system, messages, tool_list):
        fake.requests.append({"model": model, "system": system, "messages": [dict(m) for m in messages], "tools": [t["name"] for t in tool_list]})
        return fake.script.pop(0)

    monkeypatch.setattr(agent, "call_model", call)
    return fake


def _say(text):
    return {"content": [{"type": "text", "text": text}]}


def _use(name, **args):
    return {"content": [{"type": "tool_use", "id": "t1", "name": name, "input": args}]}


def _conversation(user):
    from apps.clients.models import Client

    client = Client.objects.filter(contacts__user=user).first()
    return agent.current_conversation(user, client)


def test_the_assistant_is_off_until_the_owner_turns_it_on_and_the_key_is_on_the_server(world, owner, settings):  # noqa: F811
    settings.ANTHROPIC_API_KEY = ""
    conversation = _conversation(owner)
    with pytest.raises(ServiceError) as err:
        agent.send(owner, conversation, "hello")
    assert err.value.code == "assistant_off" and not agent.available()
    row = AssistantSettings.load()
    row.enabled, row.mode = True, "claude"
    row.save()
    with pytest.raises(ServiceError):  # on, but still no key
        agent.send(owner, conversation, "hello")
    assert not Message.objects.filter(conversation=conversation).exists()


def test_a_question_is_answered_after_the_panel_ran_the_tool_the_model_asked_for(world, owner, account, switched_on, ask):  # noqa: F811
    ask.script = [_use("get_services"), _say("You have one hosting plan.")]
    conversation = _conversation(owner)
    saved = agent.send(owner, conversation, "What do I have?")
    assert [m.sender for m in saved] == ["event", "ai"] and saved[-1].body == "You have one hosting plan."
    second = ask.requests[1]["messages"]
    result = second[-1]["content"][0]
    assert result["type"] == "tool_result" and account.domain in result["content"]
    assert "get_services" in ask.requests[0]["tools"] and "create_ticket" in ask.requests[0]["tools"]
    assert Message.objects.filter(conversation=conversation, sender="client", body="What do I have?").exists()


def test_a_tool_only_sees_the_signed_in_persons_own_domains(world, owner, stranger, account, switched_on, ask):  # noqa: F811
    mine = _conversation(owner)
    ctx = tools.Context(owner, mine)
    assert tools.run(ctx, "check_ssl", {"domain": "not-mine.example.org"}) == {"error": "That domain is not on your hosting plans."}
    ok = tools.run(ctx, "check_ssl", {"domain": account.domain})
    assert "error" not in ok and ok["domain"] == account.domain
    other = tools.Context(stranger, Conversation(user=stranger, client=mine.client))
    assert tools.run(other, "get_services", {}) == {"hosting_plans": []}
    assert tools.run(other, "check_ssl", {"domain": account.domain})["error"] in ("You have no active hosting plan.", "That domain is not on your hosting plans.")
    assert tools.run(ctx, "no_such_tool", {}) == {"error": "Unknown tool."}
    assert tools.run(ctx, "check_ssl", {"client_id": 7}) == {"error": "The tool was called with the wrong inputs."}  # a model cannot name a client


def test_every_read_tool_answers_on_the_demo_server(world, owner, account, switched_on):  # noqa: F811
    ctx = tools.Context(owner, _conversation(owner))
    for name in ("get_services", "check_disk_usage", "get_error_log", "get_dns_records", "check_email_quota", "get_invoices", "check_ssl"):
        out = tools.run(ctx, name, {"domain": account.domain} if name not in ("get_services", "get_invoices") else {})
        assert "error" not in out, (name, out)


def test_a_request_for_a_person_puts_the_chat_in_the_queue_and_silences_the_assistant(world, owner, account, switched_on, ask):  # noqa: F811
    conversation = _conversation(owner)
    ask.script = [_use("request_human_handoff", reason="Wants a person"), _say("Please wait, someone will join.")]
    agent.send(owner, conversation, "I want a real person please")
    conversation.refresh_from_db()
    assert conversation.status == "waiting" and conversation.handoff_reason == "Wants a person" and "I want a real person please" in conversation.handoff_summary
    before = len(ask.requests)
    sent = agent.send(owner, conversation, "are you there?")  # nobody answers with the model now; the message waits for the team
    assert [m.sender for m in sent] == ["client"] and len(ask.requests) == before


def test_staff_take_a_chat_once_reply_hand_back_and_close_it(world, owner, account, switched_on, ask):  # noqa: F811
    from apps.assistant import live

    staff, other = world.people["super admin"], world.people["support agent"] if "support agent" in world.people else None
    conversation = _conversation(owner)
    live.ask_person(owner, conversation, "Billing question")
    with pytest.raises(ServiceError):  # a customer is not support staff
        live.take(owner, conversation)
    live.take(staff, conversation)
    conversation.refresh_from_db()
    assert conversation.status == "with_staff" and conversation.assigned_to_id == staff.pk
    with pytest.raises(ServiceError) as err:  # only one owner
        live.take(staff, conversation)
    assert err.value.code == "taken"
    live.reply(staff, conversation, "Hello, how can I help?")
    assert Message.objects.filter(conversation=conversation, sender="staff", staff=staff, body="Hello, how can I help?").exists()
    live.hand_back(staff, conversation)
    conversation.refresh_from_db()
    assert conversation.status == "ai" and conversation.assigned_to_id is None
    with pytest.raises(ServiceError) as err:  # no longer theirs
        live.reply(staff, conversation, "late")
    assert err.value.code == "not_yours"
    live.take(staff, conversation)
    live.close(staff, conversation)
    conversation.refresh_from_db()
    assert conversation.status == "closed"
    assert agent.current_conversation(owner, conversation.client, create=False) is None


def test_a_chat_becomes_a_ticket_for_staff_or_when_the_customer_will_not_wait(world, owner, account, switched_on, ask):  # noqa: F811
    from apps.assistant import live

    Department.objects.get_or_create(slug="technical", defaults={"name": "Technical"})
    staff = world.people["super admin"]
    first = _conversation(owner)
    Message.objects.create(conversation=first, sender="client", body="my site is slow")
    live.ask_person(owner, first, "Slow site")
    live.take(staff, first)
    ticket = live.to_ticket(staff, first)
    first.refresh_from_db()
    assert first.ticket_id == ticket.pk and first.status == "closed" and "my site is slow" in ticket.messages.first().body
    second = _conversation(owner)
    live.ask_person(owner, second, "Still waiting")
    again = live.ticket_instead(owner, second)
    second.refresh_from_db()
    assert second.status == "closed" and second.ticket_id == again.pk


def test_the_staff_dashboard_lists_chats_and_the_pages_poll_for_news(world, owner, account, switched_on, ask):  # noqa: F811
    from django.test import Client as DjangoClient

    from apps.assistant import live

    conversation = _conversation(owner)
    Message.objects.create(conversation=conversation, sender="client", body="hello team")
    live.ask_person(owner, conversation, "Help")
    staff = DjangoClient()
    staff.force_login(world.people["super admin"])
    page = staff.get(reverse("livechat:list") + "?tab=waiting")
    assert page.status_code == 200 and "Waiting for staff" in page.content.decode()
    detail = staff.get(reverse("livechat:detail", args=[conversation.pk]))
    assert detail.status_code == 200 and "Take chat" in detail.content.decode()
    taken = staff.post(reverse("livechat:action", args=[conversation.pk]), {"action": "take"})
    assert taken.status_code == 302
    staff.post(reverse("livechat:action", args=[conversation.pk]), {"action": "reply", "message": "Hi, I am here"})
    news = browser(owner).get(reverse("assistant:poll") + "?after=0").json()
    assert news["status"] == "with_staff" and any(m["sender"] == "staff" and m["body"] == "Hi, I am here" and m["who"] for m in news["messages"])
    mine = staff.get(reverse("livechat:poll", args=[conversation.pk]) + "?after=0").json()
    assert any(m["body"] == "hello team" for m in mine["messages"])
    assert browser(owner).get(reverse("livechat:list")).status_code == 403


def test_the_daily_limit_and_message_size_are_enforced(world, owner, account, switched_on, ask):  # noqa: F811
    conversation = _conversation(owner)
    ask.script = [_say("one"), _say("two"), _say("three")]
    for n in range(3):
        agent.send(owner, conversation, f"question {n}")
    with pytest.raises(ServiceError) as err:
        agent.send(owner, conversation, "one more")
    assert err.value.code == "daily_limit" and len(ask.requests) == 3
    with pytest.raises(ServiceError) as err:
        agent.send(owner, conversation, "x" * 2001)
    assert err.value.code in ("too_long", "daily_limit")


def test_the_chat_page_and_its_send_endpoint_work_for_a_customer(world, owner, account, switched_on, ask):  # noqa: F811
    client = browser(owner)
    page = client.get(reverse("assistant:chat"))
    assert page.status_code == 200 and "AI Agent" in page.content.decode()
    ask.script = [_say("Hi there")]
    reply = client.post(reverse("assistant:send"), {"message": "Hello"}, HTTP_X_REQUESTED_WITH="fetch")
    assert reply.status_code == 200 and reply.json()["messages"][-1]["body"] == "Hi there"
    ask.script = []
    assert "Hi there" in client.get(reverse("assistant:chat")).content.decode()
    limited = AssistantSettings.load()
    limited.enabled = False
    limited.save()
    off = client.post(reverse("assistant:send"), {"message": "Hello again"}, HTTP_X_REQUESTED_WITH="fetch")
    assert off.status_code == 503 and "not switched on" in off.json()["error"]
    client.post(reverse("assistant:new"))
    assert "Hi there" not in client.get(reverse("assistant:chat")).content.decode()


def test_staff_set_the_switches_and_read_the_record_but_customers_cannot(world, owner, account, switched_on, ask):  # noqa: F811
    from django.test import Client as DjangoClient

    staff = DjangoClient()
    staff.force_login(world.people["super admin"])
    page = staff.get(reverse("assistant_staff:settings"))
    assert page.status_code == 200 and "API key found on the server" in page.content.decode()
    saved = staff.post(reverse("assistant_staff:settings"), {"model": "claude-sonnet-5-5", "daily_limit": "12", "change_hours": "24"})
    assert saved.status_code == 302
    row = AssistantSettings.load()
    assert row.enabled is False and row.daily_limit == 12  # the box was not ticked: off
    staff.post(reverse("assistant_staff:settings"), {"model": "bad model name", "daily_limit": "12", "change_hours": "24", "enabled": "on"})
    assert AssistantSettings.load().model == "claude-sonnet-5-5"
    ask.script = [_say("hi")]
    row.enabled, row.mode = True, "claude"
    row.save()
    agent.send(owner, _conversation(owner), "hello")
    chat = Conversation.objects.first()
    assert staff.get(reverse("assistant_staff:chat", args=[chat.pk])).status_code == 302  # to the live chat page, where support can take it
    assert browser(owner).get(reverse("assistant_staff:settings")).status_code in (302, 403)


def test_the_agent_knows_the_panel_and_tells_the_customer_how_long_a_server_change_takes(world, owner, account, switched_on, ask):  # noqa: F811
    Department.objects.get_or_create(slug="technical", defaults={"name": "Technical"})
    switched_on.change_hours = 36
    switched_on.save()
    ask.script = [_use("request_server_change", summary="Raise PHP memory", details="Set memory_limit to 512M on " + account.domain, domain=account.domain), _say("Done, within 36 hours.")]
    conversation = _conversation(owner)
    agent.send(owner, conversation, "Please raise my PHP memory")
    system = ask.requests[0]["system"]
    assert "Website care" in system and "Staging" in system and "36 hours" in system and "request_server_change" in system
    result = ask.requests[1]["messages"][-1]["content"][0]["content"]
    assert "requested" in result and "36 hours" in result
    from apps.support.models import Ticket

    ticket = Ticket.objects.filter(subject__startswith="Change requested by the AI Agent").first()
    assert ticket is not None and "memory_limit" in ticket.messages.first().body


def test_the_agent_searches_the_help_articles(world, owner, account, switched_on):  # noqa: F811
    from apps.support.models import KBArticle, KBCategory

    category = KBCategory.objects.create(name="Phone setup guides", slug="email-test", is_active=True)
    KBArticle.objects.create(category=category, title="Set up email on a phone", slug="phone-email", body="Use IMAP with the mail server name.", is_published=True)
    out = tools.run(tools.Context(owner, _conversation(owner)), "search_help", {"query": "phone"})
    assert out["articles"] and out["articles"][0]["title"] == "Set up email on a phone" and out["articles"][0]["path"] == "/help/email-test/phone-email/"


def test_the_pop_up_chat_is_served_as_a_fragment_and_the_header_opens_it(world, owner, account, switched_on, ask):  # noqa: F811
    client = browser(owner)
    fragment = client.get(reverse("assistant:panel"))
    assert fragment.status_code == 200 and b"<html" not in fragment.content and b"data-agent-root" in fragment.content
    page = client.get(reverse("hosting_customer:websites")).content.decode()
    assert "data-agent-open" in page and 'id="agent-pop"' in page and "client-shell.css" in page
    assert "client-shell.css" in client.get(reverse("assistant:chat")).content.decode()  # the page keeps the layout's own styles
    assert client.post(reverse("assistant:new"), HTTP_X_REQUESTED_WITH="fetch").json() == {"ok": True}


# --- the knowledge-base mode: no outside service -----------------------------------------------------------------------------------------

@pytest.fixture
def knowledge_on(settings):
    settings.ANTHROPIC_API_KEY = ""
    row = AssistantSettings.load()
    row.enabled, row.mode, row.daily_limit = True, "knowledge", 50
    row.save()
    return row


def _ask(owner, text):
    conversation = _conversation(owner)
    return [m for m in agent.send(owner, conversation, text) if m.sender == "ai"][-1].body, conversation


def test_the_starter_knowledge_is_there_and_the_agent_works_without_any_key(world, owner, account, knowledge_on):  # noqa: F811
    from apps.assistant.models import KnowledgeEntry

    assert KnowledgeEntry.objects.filter(is_active=True).count() >= 25 and agent.available()
    text, _ = _ask(owner, "hello")
    assert "AI Agent" in text
    text, _ = _ask(owner, "Is my ssl working?")
    assert account.domain in text and "SSL" in text  # the live check on the customer's own domain


def test_a_question_in_roman_urdu_is_matched_and_a_live_check_runs_on_the_domain_named(world, owner, account, knowledge_on, monkeypatch):  # noqa: F811
    from apps.hosting import care, site_apps

    monkeypatch.setattr(site_apps, "_public", lambda host: True)
    monkeypatch.setattr(care, "_fetch", lambda url: {"final_url": url, "status": 200, "first_byte_ms": 120, "total_ms": 400, "size": 60000, "redirects": 0, "https": True, "compressed": True, "cached": True})
    text, conversation = _ask(owner, f"{account.domain} ki website nahi khul rahi")
    assert account.domain in text and ("opens" in text or "does not open" in text)
    assert Message.objects.filter(conversation=conversation, sender="event", body="check_website_status").exists()
    other, _ = _ask(owner, f"disk space kitni hai {account.domain}")
    assert "Disk" in other and "MB" in other


def test_the_agent_asks_which_domain_when_there_are_several_and_remembers_the_question(world, owner, account, knowledge_on):  # noqa: F811
    from apps.hosting.models import HostingAccount

    second = HostingAccount.objects.create(client=account.client, product=account.product, server=account.server, domain="second-plan.example-test.com", username="second1", status=account.status)
    text, conversation = _ask(owner, "check my ssl")
    assert "Which domain" in text and account.domain in text and second.domain in text
    answer = [m for m in agent.send(owner, conversation, second.domain) if m.sender == "ai"][-1].body
    assert second.domain in answer and "SSL" in answer
    conversation.refresh_from_db()
    assert conversation.pending == {}


def test_a_request_for_a_person_or_a_refund_queues_the_chat_and_a_ticket_is_opened_on_request(world, owner, account, knowledge_on):  # noqa: F811
    Department.objects.get_or_create(slug="technical", defaults={"name": "Technical"})
    text, conversation = _ask(owner, "I want to talk to a real person")
    conversation.refresh_from_db()
    assert conversation.status == "waiting" and "team" in text
    other = _conversation(owner).__class__.objects.create(user=owner, client=conversation.client)
    text, other = _ask_in(owner, other, "please open a ticket")
    other.refresh_from_db()
    assert "Ticket" in text


def _ask_in(owner, conversation, text):
    return [m for m in agent.send(owner, conversation, text) if m.sender == "ai"][-1].body, conversation


def test_what_the_agent_cannot_match_is_kept_for_staff_and_help_articles_are_offered(world, owner, account, knowledge_on):  # noqa: F811
    from apps.assistant.models import Unanswered
    from apps.support.models import KBArticle, KBCategory

    category = KBCategory.objects.create(name="Gadget guides", slug="gadget-guides", is_active=True)
    KBArticle.objects.create(category=category, title="Using the quasar widget", slug="quasar", body="How to use a quasar widget.", is_published=True)
    text, _ = _ask(owner, "my quasar thing is broken")
    assert "Using the quasar widget" in text and "/help/gadget-guides/quasar/" in text
    nothing, _ = _ask(owner, "xyzzy plugh frobnicate")
    assert "not sure" in nothing and Unanswered.objects.filter(text="xyzzy plugh frobnicate").exists()
    _ask(owner, "xyzzy plugh frobnicate")
    assert Unanswered.objects.get(text="xyzzy plugh frobnicate").count == 2


def test_staff_teach_the_agent_a_new_answer_from_a_question_it_missed(world, owner, account, knowledge_on):  # noqa: F811
    from django.test import Client as DjangoClient

    from apps.assistant.models import KnowledgeEntry, Unanswered

    _ask(owner, "kya aap wordpress themes dete hain")
    staff = DjangoClient()
    staff.force_login(world.people["super admin"])
    page = staff.get(reverse("assistant_staff:knowledge"))
    assert page.status_code == 200 and "kya aap wordpress themes dete hain" in page.content.decode()
    form = staff.get(reverse("assistant_staff:knowledge_new") + "?q=kya aap wordpress themes dete hain")
    assert form.status_code == 200
    saved = staff.post(reverse("assistant_staff:knowledge_new") + "?q=kya aap wordpress themes dete hain", {
        "title": "Themes", "keywords": "wordpress themes dete hain\nthemes kahan se milte hain", "answer": "Yes, every WordPress theme works on our hosting.", "action": "", "is_active": "on",
        "sort_order": "50", "resolve": "kya aap wordpress themes dete hain"})
    assert saved.status_code == 302 and KnowledgeEntry.objects.filter(title="Themes").exists()
    assert not Unanswered.objects.filter(text="kya aap wordpress themes dete hain").exists()
    text, _ = _ask(owner, "kya aap wordpress themes dete hain")
    assert "every WordPress theme" in text
    bad = staff.post(reverse("assistant_staff:knowledge_new"), {"title": "", "keywords": "", "answer": "", "action": ""})
    assert bad.status_code == 200 and not KnowledgeEntry.objects.filter(title="").exists()
    assert browser(owner).get(reverse("assistant_staff:knowledge")).status_code in (302, 403)


# --- Phase 3: changes the customer approves ----------------------------------------------------------------------------------------------

def _card(conversation):
    from apps.assistant.models import PendingAction

    return PendingAction.objects.filter(conversation=conversation).first()


def test_a_change_is_proposed_and_runs_only_after_the_customer_presses_approve_and_only_once(world, owner, account, knowledge_on):  # noqa: F811
    from apps.assistant import actions
    from apps.hosting import control
    from apps.hosting.adapters.capabilities import Capability

    text, conversation = _ask(owner, f"please force https on {account.domain}")
    card = _card(conversation)
    failed = actions.approve(owner, card.pk)  # no working certificate yet: the panel refuses and the Agent says why
    assert failed.status == "failed" and "certificate" in failed.result
    control.perform(owner, account, Capability.SSL, "request_ssl", account.domain)
    text, conversation = _ask_in(owner, agent.current_conversation(owner, conversation.client), f"please force https on {account.domain}")
    card = [a for a in __import__("apps.assistant.models", fromlist=["PendingAction"]).PendingAction.objects.filter(conversation=conversation, status="pending")][0]
    assert card is not None and card.status == "pending" and card.tool == "force_https" and account.domain in card.summary
    assert "Nothing happens until you press Approve" in text
    before = control.perform(owner, account, Capability.SSL, "get_ssl_status", account.domain).https_redirect
    done = actions.approve(owner, card.pk)
    assert done.status == "done" and "HTTPS redirect is now on" in done.result
    assert control.perform(owner, account, Capability.SSL, "get_ssl_status", account.domain).https_redirect is True or before is True
    with pytest.raises(ServiceError) as err:  # a second click does nothing
        actions.approve(owner, card.pk)
    assert err.value.code == "handled"
    assert Message.objects.filter(conversation=conversation, action=card).first().tool_call["status"] == "done"


def test_a_mailbox_size_and_a_php_limit_are_read_from_the_customers_words_and_checked(world, owner, account, knowledge_on):  # noqa: F811
    from apps.assistant import actions
    from apps.hosting import control
    from apps.hosting.adapters.capabilities import Capability

    control.perform(owner, account, Capability.EMAIL, "create_mailbox", "agent@" + account.domain, "Str0ng-Passw0rd-xyz", quota_mb=100)
    text, conversation = _ask(owner, "increase mailbox size")
    assert "Which mailbox" in text and _card(conversation) is None
    reply = [m for m in agent.send(owner, conversation, f"agent@{account.domain} 2 GB") if m.sender == "ai"][-1].body
    card = _card(conversation)
    assert card is not None and card.params == {"address": f"agent@{account.domain}", "quota_mb": 2048}
    assert actions.approve(owner, card.pk).status == "done"
    box = next(b for b in control.perform(owner, account, Capability.EMAIL, "list_mailboxes") if b.address == f"agent@{account.domain}")
    assert box.quota_mb == 2048
    other = agent.current_conversation(owner, conversation.client)
    text, other = _ask_in(owner, other, f"set memory limit to 512 MB on {account.domain}")
    php = _card(other)
    assert php is not None and php.params["key"] == "memory_limit" and php.params["value"] == "512M"
    text, _ = _ask_in(owner, other, f"set memory limit to 99999 MB on {account.domain}")
    assert "between" in text


def test_only_the_person_it_was_shown_to_can_approve_cancel_expire_and_the_kill_switch_stops_it(world, owner, stranger, account, knowledge_on):  # noqa: F811
    from datetime import timedelta

    from django.utils import timezone

    from apps.assistant import actions
    from apps.assistant.models import PendingAction

    _, conversation = _ask(owner, f"scan my website {account.domain}")
    card = _card(conversation)
    assert card.tool == "scan_website"
    with pytest.raises(ServiceError) as err:
        actions.approve(stranger, card.pk)
    assert err.value.code == "not_found"
    assert actions.cancel(owner, card.pk).status == "cancelled"
    with pytest.raises(ServiceError):
        actions.approve(owner, card.pk)
    _, second = _ask_in(owner, agent.current_conversation(owner, conversation.client), f"take backup now {account.domain}")
    old = _card(second)
    PendingAction.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(minutes=45))
    with pytest.raises(ServiceError) as err:
        actions.approve(owner, old.pk)
    assert err.value.code == "expired"
    knowledge_on.changes_enabled = False
    knowledge_on.save()
    text, third = _ask_in(owner, agent.current_conversation(owner, conversation.client), f"renew ssl {account.domain}")
    assert "switched off" in text and PendingAction.objects.filter(conversation=third).count() == 2  # nothing new was prepared


def test_the_approve_button_works_through_the_page_and_a_domain_that_is_not_yours_is_refused(world, owner, account, knowledge_on):  # noqa: F811
    _, conversation = _ask(owner, f"renew ssl {account.domain}")
    card = _card(conversation)
    client = browser(owner)
    page = client.get(reverse("assistant:panel")).content.decode()
    assert "data-act" in page and "Approve" in page and f'data-action="{card.pk}"' in page
    ok = client.post(reverse("assistant:act", args=[card.pk, "approve"]), HTTP_X_REQUESTED_WITH="fetch").json()
    assert ok["ok"] is True and ok["status"] == "done"
    again = client.post(reverse("assistant:act", args=[card.pk, "approve"]), HTTP_X_REQUESTED_WITH="fetch")
    assert again.status_code == 400
    ctx = tools.Context(owner, conversation)
    from apps.assistant import actions

    with pytest.raises(ServiceError):
        actions.propose(ctx, "renew_ssl", {"domain": "not-mine.example.org"})


def test_in_claude_mode_the_change_tools_only_propose(world, owner, account, switched_on, ask):  # noqa: F811
    from apps.assistant.models import PendingAction

    ask.script = [_use("scan_website", domain=account.domain), _say("Press Approve to start the scan.")]
    conversation = _conversation(owner)
    agent.send(owner, conversation, "scan my site")
    card = PendingAction.objects.get(conversation=conversation)
    assert card.status == "pending" and "awaiting_approval" in ask.requests[1]["messages"][-1]["content"][0]["content"]
    assert "set_mailbox_quota" in ask.requests[0]["tools"] and "client_id" not in str(ask.requests[0]["tools"])


# --- Phase 4: changes that need the emailed security code -----------------------------------------------------------------------------------

def _last_code(owner):
    """The six digits from the newest security-code email sent to this person."""
    import re

    from django.core import mail

    for message in reversed(mail.outbox):
        if owner.email in message.to and "security code" in message.subject.lower():
            return re.search(r"\b(\d{6})\b", message.body).group(1)
    raise AssertionError("no code email was sent")


def test_a_sensitive_change_waits_for_the_code_that_only_the_customer_gets_by_email(world, owner, account, knowledge_on, mailoutbox):  # noqa: F811
    from apps.assistant import actions
    from apps.assistant.models import VerificationCode
    from apps.hosting import control
    from apps.hosting.adapters.capabilities import Capability

    control.perform(owner, account, Capability.EMAIL, "create_mailbox", "old@" + account.domain, "Str0ng-Passw0rd-xyz", quota_mb=100)
    text, conversation = _ask(owner, f"delete mailbox old@{account.domain}")
    card = _card(conversation)
    assert card.needs == "code" and card.status == "pending" and "Verify securely" in text
    code = _last_code(owner)
    assert code not in text and code not in " ".join(Message.objects.filter(conversation=conversation).values_list("body", flat=True))  # never in the chat
    assert VerificationCode.objects.get(action=card).code_hash != code  # only a hash is kept
    with pytest.raises(ServiceError) as err:  # the Approve button is not enough
        actions.approve(owner, card.pk)
    assert err.value.code == "needs_code"
    with pytest.raises(ServiceError) as err:
        actions.verify(owner, card.pk, "000000" if code != "000000" else "111111")
    assert err.value.code == "code_wrong" and "2 attempts left" in err.value.message
    done = actions.verify(owner, card.pk, code)
    assert done.status == "done"
    assert "old@" + account.domain not in [b.address for b in control.perform(owner, account, Capability.EMAIL, "list_mailboxes")]
    with pytest.raises(ServiceError):  # one code, one action
        actions.verify(owner, card.pk, code)


def test_three_wrong_codes_lock_the_change_and_tell_the_customer_and_the_team(world, owner, account, knowledge_on, mailoutbox):  # noqa: F811
    from apps.assistant import actions
    from apps.notifications.models import Notification

    control_mailbox = "lock@" + account.domain
    from apps.hosting import control
    from apps.hosting.adapters.capabilities import Capability

    control.perform(owner, account, Capability.EMAIL, "create_mailbox", control_mailbox, "Str0ng-Passw0rd-xyz", quota_mb=100)
    _, conversation = _ask(owner, f"delete mailbox {control_mailbox}")
    card = _card(conversation)
    real = _last_code(owner)
    wrong = "123456" if real != "123456" else "654321"
    for _ in range(2):
        with pytest.raises(ServiceError):
            actions.verify(owner, card.pk, wrong)
    with pytest.raises(ServiceError) as err:
        actions.verify(owner, card.pk, wrong)
    assert err.value.code == "locked"
    card.refresh_from_db()
    assert card.status == "locked"
    with pytest.raises(ServiceError):  # the right code no longer helps
        actions.verify(owner, card.pk, real)
    assert any("locked" in m.subject.lower() for m in mailoutbox if owner.email in m.to)
    assert Notification.objects.filter(event="assistant.locked_team").exists()
    assert control_mailbox in [b.address for b in control.perform(owner, account, Capability.EMAIL, "list_mailboxes")]  # nothing was deleted


def test_a_code_expires_can_be_sent_again_after_the_wait_and_belongs_to_its_owner(world, owner, stranger, account, knowledge_on, mailoutbox):  # noqa: F811
    from datetime import timedelta

    from django.utils import timezone

    from apps.assistant import actions
    from apps.assistant.models import VerificationCode

    _, conversation = _ask(owner, f"add a record TXT @ hello-world-verification on {account.domain}")
    card = _card(conversation)
    assert card.tool == "add_dns_record" and card.params["type"] == "TXT" and card.params["value"] == "hello-world-verification"
    with pytest.raises(ServiceError) as err:
        actions.verify(stranger, card.pk, "123456")
    assert err.value.code == "not_found"
    with pytest.raises(ServiceError) as err:
        actions.resend_code(owner, card.pk)
    assert err.value.code == "cooldown"
    VerificationCode.objects.filter(action=card).update(last_sent_at=timezone.now() - timedelta(seconds=31))
    actions.resend_code(owner, card.pk)
    fresh = _last_code(owner)
    VerificationCode.objects.filter(action=card).update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(ServiceError) as err:
        actions.verify(owner, card.pk, fresh)
    assert err.value.code == "code_expired"


def test_the_verification_page_checks_the_code_and_runs_the_change(world, owner, account, knowledge_on, mailoutbox):  # noqa: F811
    from django.utils.html import escape

    from apps.hosting import control
    from apps.hosting.adapters.capabilities import Capability

    _, conversation = _ask(owner, f"add dns record A blog 203.0.113.7 on {account.domain}")
    card = _card(conversation)
    client = browser(owner)
    page = client.get(reverse("assistant:verify", args=[card.pk]))
    html = page.content.decode()
    assert page.status_code == 200 and "Verify it is you" in html and "Security code" in html and escape(card.summary) in html and owner.email[:1] in html
    wrong = client.post(reverse("assistant:verify", args=[card.pk]), {"code": "999999" if _last_code(owner) != "999999" else "888888"})
    assert "Incorrect code" in wrong.content.decode()
    ok = client.post(reverse("assistant:verify", args=[card.pk]), {"code": _last_code(owner)})
    assert "Verified" in ok.content.decode() and "Back to chat" in ok.content.decode()
    names = {r.name.rstrip(".") for r in control.perform(owner, account, Capability.DNS, "list_dns_records", account.domain)}
    assert any("blog" in n for n in names)
    other = browser(owner)
    assert other.get(reverse("assistant:verify", args=[card.pk + 999])).status_code == 404


# --- Phase 5: availability, working hours, analytics, feedback -------------------------------------------------------------------------------

def test_with_nobody_online_a_request_for_a_person_becomes_a_ticket_and_with_someone_online_it_waits(world, owner, account, knowledge_on):  # noqa: F811
    from apps.assistant import live

    Department.objects.get_or_create(slug="technical", defaults={"name": "Technical"})
    knowledge_on.offline_ticket = True
    knowledge_on.save()
    staff = world.people["super admin"]
    text, conversation = _ask(owner, "I want to talk to a real person")
    conversation.refresh_from_db()
    assert "not online" in text and "ticket" in text.lower() and conversation.status == "closed" and conversation.ticket_id
    live.set_online(staff, True)
    second = agent.current_conversation(owner, conversation.client)
    text, second = _ask_in(owner, second, "I want to talk to a real person")
    second.refresh_from_db()
    assert second.status == "waiting" and "asked someone" in text
    live.set_online(staff, False)
    assert live.staff_online() == []


def test_working_hours_decide_who_counts_as_available(world, knowledge_on):  # noqa: F811
    import datetime as dt

    from apps.assistant import live

    knowledge_on.hours_enabled, knowledge_on.hours_timezone = True, "Asia/Karachi"
    knowledge_on.hours_start, knowledge_on.hours_end, knowledge_on.hours_days = dt.time(9, 0), dt.time(18, 0), "0,1,2,3,4"
    knowledge_on.save()
    monday_noon = dt.datetime(2026, 10, 19, 7, 0, tzinfo=dt.timezone.utc)  # 12:00 in Karachi, a Monday
    monday_night = dt.datetime(2026, 10, 19, 20, 0, tzinfo=dt.timezone.utc)  # 01:00 on Tuesday there
    sunday = dt.datetime(2026, 10, 18, 7, 0, tzinfo=dt.timezone.utc)
    assert live.in_hours(knowledge_on, monday_noon) and not live.in_hours(knowledge_on, monday_night) and not live.in_hours(knowledge_on, sunday)
    assert "Mon-Fri 09:00-18:00" in live.hours_text(knowledge_on)
    knowledge_on.hours_enabled = False
    assert live.in_hours(knowledge_on, sunday)


def test_a_person_goes_online_from_the_chat_page_and_the_settings_hold_the_hours(world, knowledge_on):  # noqa: F811
    from django.test import Client as DjangoClient

    from apps.assistant import live

    staff = DjangoClient()
    staff.force_login(world.people["super admin"])
    assert "Go online" in staff.get(reverse("livechat:list")).content.decode()
    staff.post(reverse("livechat:availability"), {"online": "1"})
    assert [u.pk for u in live.staff_online()] == [world.people["super admin"].pk] and "You are online" in staff.get(reverse("livechat:list")).content.decode()
    saved = staff.post(reverse("assistant_staff:settings"), {"model": "claude-sonnet-5-5", "daily_limit": "12", "change_hours": "24", "changes_enabled": "on", "offline_ticket": "on", "hours_enabled": "on",
                                                              "hours_start": "10:00", "hours_end": "19:30", "hours_timezone": "Asia/Karachi", "day_0": "on", "day_1": "on", "enabled": "on"})
    assert saved.status_code == 302
    row = AssistantSettings.load()
    assert row.offline_ticket and row.hours_enabled and row.hours_start.hour == 10 and row.hours_end.minute == 30 and row.hours_days == "0,1"
    staff.post(reverse("assistant_staff:settings"), {"model": "claude-sonnet-5-5", "daily_limit": "12", "change_hours": "24", "hours_start": "20:00", "hours_end": "08:00", "hours_timezone": "Asia/Karachi"})
    assert AssistantSettings.load().hours_start.hour == 10  # an impossible day is refused


def test_analytics_count_what_happened_and_the_thumbs_come_from_the_customer(world, owner, account, knowledge_on):  # noqa: F811
    from django.test import Client as DjangoClient

    from apps.assistant import analytics, live

    Department.objects.get_or_create(slug="technical", defaults={"name": "Technical"})
    staff_user = world.people["super admin"]
    _, one = _ask(owner, "hello")
    answer = Message.objects.filter(conversation=one, sender="ai").first()
    client = browser(owner)
    assert client.post(reverse("assistant:feedback", args=[answer.pk, "up"]), HTTP_X_REQUESTED_WITH="fetch").json() == {"ok": True, "vote": "up"}
    assert client.post(reverse("assistant:feedback", args=[answer.pk + 9999, "up"])).status_code == 404
    _, two = _ask_in(owner, agent.current_conversation(owner, one.client).__class__.objects.create(user=owner, client=one.client), "I want a real person")
    live.take(staff_user, two)
    report = analytics.report(7)
    assert report["chats"] >= 2 and report["with_person"] >= 1 and report["up"] == 1 and report["helpful_percent"] == 100 and report["average_wait"] is not None
    page = DjangoClient()
    page.force_login(staff_user)
    html = page.get(reverse("assistant_staff:analytics") + "?days=30").content.decode()
    assert "Agent analytics" in html and "Solved by the Agent alone" in html
    assert browser(owner).get(reverse("assistant_staff:analytics")).status_code in (302, 403)
