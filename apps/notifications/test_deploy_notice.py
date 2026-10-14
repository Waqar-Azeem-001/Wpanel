"""The pipeline's result reaches the team through WHPanel's own email provider and alert addresses."""
import pytest
from django.core import mail
from django.core.management import call_command

from apps.notifications import events
from apps.notifications.models import NotificationSettings

pytestmark = pytest.mark.django_db


def test_the_event_is_registered_and_has_its_own_switch():
    assert "system.deploy" in events.EVENTS
    assert NotificationSettings.load().alert_deploys is True


def test_a_published_version_is_mailed_to_the_alert_addresses(settings, staff):
    from apps.accounts.roles import Role

    staff(Role.MANAGER)
    NotificationSettings.objects.update_or_create(pk=1, defaults={"alert_emails": "owner@gmail.example.com"})
    call_command("notify_deploy", "--tests", "success", "--deploy", "success", "--sha", "4441f28aaaa", "--message", "Delete website", "--run-url", "https://ci.example/run/1")
    outbox = [m for m in mail.outbox if "owner@gmail.example.com" in m.to]
    assert outbox and "New version published" in outbox[0].subject
    body = outbox[0].body
    assert "Tests passed" in body and "Deploy to production passed" in body and "4441f28aaaa"[:12] in body


def test_a_failed_run_says_so(settings, staff):
    NotificationSettings.objects.update_or_create(pk=1, defaults={"alert_emails": "owner@gmail.example.com"})
    call_command("notify_deploy", "--tests", "failure", "--deploy", "skipped")
    sent = [m for m in mail.outbox if "owner@gmail.example.com" in m.to]
    assert sent and "needs attention" in sent[0].subject and "FAILED" in sent[0].body


def test_the_switch_turns_it_off(settings):
    NotificationSettings.objects.update_or_create(pk=1, defaults={"alert_emails": "owner@gmail.example.com", "alert_deploys": False})
    call_command("notify_deploy", "--tests", "success", "--deploy", "success")
    assert not [m for m in mail.outbox if "owner@gmail.example.com" in m.to]
