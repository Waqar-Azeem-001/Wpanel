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
    row.enabled, row.daily_limit = True, 3
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
    row.enabled = True
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


def test_a_handoff_opens_one_ticket_with_the_chat_attached(world, owner, account, switched_on, ask):  # noqa: F811
    Department.objects.get_or_create(slug="technical", defaults={"name": "Technical"})
    conversation = _conversation(owner)
    ask.script = [_use("request_human_handoff", reason="Wants a person"), _say("I opened a ticket for you.")]
    agent.send(owner, conversation, "I want a real person please")
    conversation.refresh_from_db()
    assert conversation.ticket_id and conversation.status == "waiting"
    message = conversation.ticket.messages.first().body
    assert "I want a real person please" in message and "Wants a person" in message
    again = tools.run(tools.Context(owner, conversation), "request_human_handoff", {"reason": "again"})
    assert again["status"] == "already_with_support"
    with pytest.raises(ServiceError) as err:  # the chat now belongs to the team
        agent.send(owner, conversation, "hello?")
    assert err.value.code == "with_staff"


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
    assert page.status_code == 200 and "AI assistant" in page.content.decode()
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
    saved = staff.post(reverse("assistant_staff:settings"), {"model": "claude-sonnet-5-5", "daily_limit": "12"})
    assert saved.status_code == 302
    row = AssistantSettings.load()
    assert row.enabled is False and row.daily_limit == 12  # the box was not ticked: off
    staff.post(reverse("assistant_staff:settings"), {"model": "bad model name", "daily_limit": "12", "enabled": "on"})
    assert AssistantSettings.load().model == "claude-sonnet-5-5"
    ask.script = [_say("hi")]
    row.enabled = True
    row.save()
    agent.send(owner, _conversation(owner), "hello")
    chat = Conversation.objects.first()
    assert staff.get(reverse("assistant_staff:chat", args=[chat.pk])).status_code == 200
    assert browser(owner).get(reverse("assistant_staff:settings")).status_code in (302, 403)
