import pytest
from django.test import RequestFactory

from apps.audit import services
from apps.audit.models import AuditEvent
from apps.core.request_context import request_id_var

pytestmark = pytest.mark.django_db


def test_record_captures_actor_target_request_context(customer):
    request = RequestFactory().get("/", HTTP_USER_AGENT="pytest", REMOTE_ADDR="10.0.0.5")
    token = request_id_var.set("req-42")
    try:
        event = services.record("thing.done", actor=customer, target=customer, metadata={"k": 1}, request=request)
    finally:
        request_id_var.reset(token)
    assert event.actor == customer and event.actor_repr == customer.email
    assert (event.target_type, event.target_id) == ("accounts.user", str(customer.pk))
    assert (event.ip_address, event.user_agent, event.request_id) == ("10.0.0.5", "pytest", "req-42")


def test_record_redacts_secrets():
    event = services.record("x", metadata={"password": "p", "token": "t", "api_key": "k", "safe": "ok"})
    assert event.metadata == {"password": "[redacted]", "token": "[redacted]", "api_key": "[redacted]", "safe": "ok"}


def test_system_action_has_no_actor():
    event = services.record("system.job")
    assert event.actor is None and event.actor_repr == ""
    assert AuditEvent.objects.count() == 1
