"""The AI Agent's changes, checked directly: what each request must contain, what it is refused for, and what the customer is told when the server says no."""
from types import SimpleNamespace

import pytest

from apps.assistant import actions, tools
from apps.assistant.models import AssistantSettings, Conversation, PendingAction
from apps.core.exceptions import ServiceError
from apps.core.test_client_area import owner, stranger  # noqa: F401
from apps.core.test_client_shell import account  # noqa: F401
from apps.hosting import control
from apps.hosting.adapters.capabilities import Capability

pytestmark = pytest.mark.django_db


@pytest.fixture
def ctx(world, owner, account):  # noqa: F811
    row = AssistantSettings.load()
    row.enabled, row.mode, row.changes_enabled = True, "knowledge", True
    row.save()
    conversation = Conversation.objects.create(client=account.client, user=owner)
    return tools.Context(owner, conversation)


def refused(code, call, *args):
    with pytest.raises(ServiceError) as caught:
        call(*args)
    assert caught.value.code == code, (caught.value.code, caught.value.message)


# --- mailbox size and PHP limits ---------------------------------------------------------------------------------------------------------

def test_a_mailbox_size_must_be_a_number_inside_the_allowed_range(ctx, account):  # noqa: F811
    refused("quota_needed", actions._quota, ctx, {"address": "a@x.example", "quota_mb": "lots"})
    refused("quota_needed", actions._quota, ctx, {"address": "a@x.example"})
    refused("quota_range", actions._quota, ctx, {"address": "a@x.example", "quota_mb": 10})
    refused("quota_range", actions._quota, ctx, {"address": "a@x.example", "quota_mb": 99999})
    refused("not_yours", actions._quota, ctx, {"address": "ghost@" + account.domain, "quota_mb": 500})


def test_a_mailbox_on_a_server_that_cannot_list_is_skipped_not_fatal(ctx, monkeypatch):
    def down(*args, **kwargs):
        raise ServiceError("server busy", code="adapter_error")

    monkeypatch.setattr(control, "perform", down)
    refused("not_yours", actions._quota, ctx, {"address": "a@x.example", "quota_mb": 500})
    refused("not_yours", actions._mailbox_delete, ctx, {"address": "a@x.example"})
    refused("backup_missing", actions._restore, ctx, {"backup_id": "b1"})


def test_a_php_setting_must_be_known_numeric_and_in_range(ctx, account):  # noqa: F811
    d = account.domain
    refused("php_key", actions._php, ctx, {"domain": d, "key": "disable_functions", "value": "1"})
    refused("php_value", actions._php, ctx, {"domain": d, "key": "memory_limit", "value": "plenty"})
    refused("php_range", actions._php, ctx, {"domain": d, "key": "memory_limit", "value": "8"})
    refused("php_range", actions._php, ctx, {"domain": d, "key": "max_execution_time", "value": "9000"})
    _, clean, summary = actions._php(ctx, {"domain": d, "key": "memory_limit", "value": "256 MB"})
    assert clean["value"] == "256M" and "memory limit" in summary
    _, clean, summary = actions._php(ctx, {"domain": d, "key": "max_execution_time", "value": "120"})
    assert clean["value"] == 120 and "120 seconds" in summary  # a time has no unit, so it is a plain number


# --- DNS ----------------------------------------------------------------------------------------------------------------------------------

def test_a_dns_record_needs_a_known_type_and_a_sensible_value(ctx, account):  # noqa: F811
    d = account.domain
    refused("dns_type", actions._dns_add, ctx, {"domain": d, "type": "SRV", "value": "x"})
    refused("dns_value", actions._dns_add, ctx, {"domain": d, "type": "A", "value": ""})
    refused("dns_value", actions._dns_add, ctx, {"domain": d, "type": "TXT", "value": "a\nb"})
    refused("dns_value", actions._dns_add, ctx, {"domain": d, "type": "TXT", "value": "x" * 401})
    refused("dns_value", actions._dns_add, ctx, {"domain": d, "type": "A", "value": "203.0.113.5 extra"})
    _, txt, _ = actions._dns_add(ctx, {"domain": d, "type": "txt", "name": "", "value": "v=spf1 -all"})
    assert txt["type"] == "TXT" and txt["name"] == "@" and txt["priority"] is None  # spaces are fine in text; an empty name means the domain itself


def test_an_mx_record_gets_priority_ten_unless_the_customer_gave_another(ctx, account):  # noqa: F811
    d = account.domain
    assert actions._dns_add(ctx, {"domain": d, "type": "MX", "name": "@", "value": "mail.x.example"})[1]["priority"] == 10
    assert actions._dns_add(ctx, {"domain": d, "type": "MX", "name": "@", "value": "mail.x.example", "priority": "20"})[1]["priority"] == 20
    assert actions._dns_add(ctx, {"domain": d, "type": "MX", "name": "@", "value": "mail.x.example", "priority": "high"})[1]["priority"] == 10


def test_deleting_a_dns_record_finds_it_by_name_and_never_touches_ns_or_soa(ctx, account, monkeypatch):  # noqa: F811
    d = account.domain
    rows = [SimpleNamespace(record_id="1", type="A", name=f"www.{d}.", value="203.0.113.5"),
            SimpleNamespace(record_id="2", type="NS", name=d, value="ns1.x.example"),
            SimpleNamespace(record_id="3", type="SOA", name=d, value="soa")]
    monkeypatch.setattr(control, "perform", lambda *a, **k: rows)
    _, clean, summary = actions._dns_delete(ctx, {"domain": d, "type": "a", "name": "www"})
    assert clean["record_id"] == "1" and "www" in summary
    refused("dns_missing", actions._dns_delete, ctx, {"domain": d, "type": "NS", "name": d})  # the server's own records stay
    refused("dns_missing", actions._dns_delete, ctx, {"domain": d, "type": "A", "name": "nothing"})
    refused("dns_missing", actions._dns_delete, ctx, {"domain": d})


# --- backups and mailboxes ---------------------------------------------------------------------------------------------------------------

def test_a_restore_only_names_a_backup_that_is_on_the_customers_own_account(ctx, account, monkeypatch):  # noqa: F811
    refused("backup_missing", actions._restore, ctx, {"backup_id": ""})
    monkeypatch.setattr(control, "perform", lambda *a, **k: [SimpleNamespace(backup_id="b1", kind="full", created_at="2026-10-01")])
    got_account, clean, summary = actions._restore(ctx, {"backup_id": "b1"})
    assert clean == {"backup_id": "b1"} and "full backup b1" in summary
    refused("backup_missing", actions._restore, ctx, {"backup_id": "someone-elses"})


# --- when the server says no, or the account has gone ---------------------------------------------------------------------------------

def test_a_change_that_the_server_refuses_is_marked_failed_and_the_customer_is_told(ctx, owner, account, monkeypatch):  # noqa: F811
    action = actions.propose(ctx, "scan_website", {"domain": account.domain})

    def refuses(*args, **kwargs):
        raise ServiceError("The scanner is not installed.", code="adapter_error")

    monkeypatch.setattr(actions.care, "start_scan", refuses)
    done = actions.approve(owner, action.pk)
    assert done.status == "failed" and "scanner is not installed" in done.result
    assert ctx.conversation.messages.filter(sender="ai", body__startswith="That did not work").exists()


def test_a_change_for_an_account_the_customer_no_longer_has_fails_cleanly(ctx, owner, account):  # noqa: F811
    action = actions.propose(ctx, "scan_website", {"domain": account.domain})
    PendingAction.objects.filter(pk=action.pk).update(account_id=0)
    done = actions.approve(owner, action.pk)
    assert done.status == "failed" and "no longer available" in done.result


def test_a_scan_of_the_main_domain_and_a_backup_use_the_servers_own_choices(ctx, owner, account, monkeypatch):  # noqa: F811
    seen = {}
    monkeypatch.setattr(actions.care, "start_scan", lambda user, acct, domain="": seen.update(domain=domain))
    assert "started" in actions.run_scan(owner, account, {"domain": account.domain.lower()})
    assert seen["domain"] == ""  # the main domain is scanned as the whole account
    actions.run_scan(owner, account, {"domain": "blog." + account.domain})
    assert seen["domain"] == "blog." + account.domain
    calls = []

    def perform(user, acct, capability, operation, *args, **kwargs):
        calls.append((operation, args))
        return SimpleNamespace(kinds=[]) if operation == "backup_options" else None

    monkeypatch.setattr(control, "perform", perform)
    assert "backup was started" in actions.run_backup(owner, account, {"domain": account.domain})
    assert ("create_backup", ("full",)) in calls  # a server that offers no choice gets a full backup
    calls.clear()
    monkeypatch.setattr(control, "perform", lambda *a, **k: SimpleNamespace(kinds=[("homedir", "Home")]) if a[3] == "backup_options" else calls.append(a[3:]))
    actions.run_backup(owner, account, {"domain": account.domain})
    assert calls == [("create_backup", "homedir")]


# --- proposing, the code and the buttons -------------------------------------------------------------------------------------------------

def test_a_change_is_refused_when_switched_off_unknown_or_given_odd_parameters(ctx, account):  # noqa: F811
    refused("unknown_change", actions.propose, ctx, "format_server", {})
    refused("dns_type", actions.propose, ctx, "add_dns_record", {"domain": account.domain, "type": "A" * 0 + "ZZ", "value": "x"})
    refused("domain_needed", actions.propose, ctx, "scan_website", ["not", "a", "dict"])  # parameters that are not a dict count as empty
    row = AssistantSettings.load()
    row.changes_enabled = False
    row.save()
    refused("changes_off", actions.propose, ctx, "scan_website", {"domain": account.domain})


def test_an_email_address_is_masked_in_the_code_notice():
    assert actions.mask("waqar@example.com") == "w****@example.com"
    assert actions.mask("ab@example.com") == "a**@example.com"
    assert actions.mask("") == "your email" and actions.mask("no-at-sign") == "your email"


def test_a_code_cannot_be_checked_on_a_change_that_needs_none_or_is_finished(ctx, owner, account, mailoutbox):  # noqa: F811
    plain = actions.propose(ctx, "scan_website", {"domain": account.domain})
    refused("no_code", actions.verify, owner, plain.pk, "123456")
    refused("not_active", actions.resend_code, owner, plain.pk)  # nothing to resend: it needs no code
    sensitive = actions.propose(ctx, "add_dns_record", {"domain": account.domain, "type": "TXT", "name": "t", "value": "v"})
    actions.cancel(owner, sensitive.pk)
    refused("not_active", actions.verify, owner, sensitive.pk, "123456")
    refused("not_active", actions.resend_code, owner, sensitive.pk)
    refused("handled", actions.cancel, owner, sensitive.pk)  # cancelling twice does nothing the second time


def test_a_code_that_is_not_six_digits_counts_as_a_wrong_try(ctx, owner, account, mailoutbox):  # noqa: F811
    action = actions.propose(ctx, "add_dns_record", {"domain": account.domain, "type": "TXT", "name": "t", "value": "v"})
    refused("code_wrong", actions.verify, owner, action.pk, "12ab")
    refused("code_wrong", actions.verify, owner, action.pk, "")
    action.verification.refresh_from_db()
    assert action.verification.attempts == 2 and PendingAction.objects.get(pk=action.pk).status == "pending"


def test_the_approve_button_will_not_run_a_change_that_needs_the_code(ctx, owner, account, mailoutbox):  # noqa: F811
    action = actions.propose(ctx, "add_dns_record", {"domain": account.domain, "type": "TXT", "name": "t", "value": "v"})
    refused("needs_code", actions.approve, owner, action.pk)
    assert PendingAction.objects.get(pk=action.pk).status == "pending"


def test_cancelling_someone_elses_request_says_it_was_not_found(ctx, stranger, account):  # noqa: F811
    action = actions.propose(ctx, "scan_website", {"domain": account.domain})
    refused("not_found", actions.cancel, stranger, action.pk)
    assert PendingAction.objects.get(pk=action.pk).status == "pending"
