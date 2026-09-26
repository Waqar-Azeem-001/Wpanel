"""Phase D4d (Phase 15 screens): staff and roles, the email provider and the registrar, without the Django admin."""
import pytest
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.test import Client as HttpClient
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.core import crypto
from apps.domains.models import Domain, RegistrarProvider
from apps.notifications.models import EmailProvider

pytestmark = pytest.mark.django_db


def browser(user=None):
    client = HttpClient(HTTP_HOST="localhost", raise_request_exception=False)
    if user is not None:
        client.force_login(user)
    return client


def notes(client, response):
    return [str(m) for m in client.get(response.headers["Location"]).context["messages"]]


# --- Staff and roles --------------------------------------------------------------------------------------------------

def test_who_may_open_the_users_screen_and_what_they_see(world):
    url = reverse("console:users") + "?role=admin"
    assert browser().get(url).status_code == 302
    for role in ("customer owner", "support agent"):
        assert browser(world.people[role]).get(url).status_code == 403, role
    manager = browser(world.people["manager"]).get(url).content.decode()
    assert "admin@harness.test" in manager and "Add a staff member" not in manager  # managers cannot assign roles
    admin_pk = world.people["support agent"].pk
    detail = browser(world.people["manager"]).get(reverse("console:user_detail", args=[admin_pk])).content.decode()
    assert 'name="role"' not in detail and "Suspend" in detail  # ...but may suspend
    admin = browser(world.people["admin"]).get(url).content.decode()
    assert "Add a staff member" in admin
    assert 'name="role"' in browser(world.people["admin"]).get(reverse("console:user_detail", args=[admin_pk])).content.decode()


def create(client, **data):
    body = {"email": "new.agent@harness.test", "first_name": "New", "last_name": "Agent", "role": "support_agent", **data}
    return client.post(reverse("console:staff_user_create"), body)


def test_an_admin_adds_an_agent_who_gets_a_link_to_set_their_own_password(world):
    mail.outbox.clear()
    client = browser(world.people["admin"])
    response = create(client)
    user = User.objects.get(email="new.agent@harness.test")
    assert response.headers["Location"] == reverse("console:user_detail", args=[user.pk])
    assert user.role == "support_agent" and user.is_staff and not user.has_usable_password()
    assert user.groups.filter(name="Support Agent").exists()
    assert AuditEvent.objects.filter(action="account.staff_created", target_id=str(user.pk)).exists()
    message = next(m for m in mail.outbox if "new.agent@harness.test" in m.to)
    assert "Support Agent" in message.body and "/account/password-reset/" in message.body
    from apps.notifications.models import EmailMessage

    stored = EmailMessage.objects.get(to_email="new.agent@harness.test")
    assert stored.is_sensitive and "password-reset" not in stored.body_text  # the secret link is not kept in the log


def test_the_link_in_the_welcome_email_lets_them_choose_a_password(world):
    create(browser(world.people["admin"]))
    user = User.objects.get(email="new.agent@harness.test")
    uid, token = urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user)
    page = browser().get(reverse("accounts:password_reset_confirm", args=[uid, token]), follow=True)
    assert page.status_code == 200 and b'type="password"' in page.content


@pytest.mark.parametrize("data,fragment", [
    ({"email": "not-an-email"}, "valid email"), ({"email": "ada@harness.test"}, "already has an account"),
    ({"role": "customer"}, "Select a valid choice"), ({"role": "made_up"}, "Select a valid choice"), ({"email": ""}, "required")])
def test_a_bad_new_member_is_refused_with_a_reason_and_nothing_is_created(world, data, fragment):
    before = User.objects.count()
    response = create(browser(world.people["admin"]), **data)
    assert response.status_code == 200 and fragment in response.content.decode() and User.objects.count() == before


def test_only_a_super_admin_can_create_an_admin(world):
    response = create(browser(world.people["admin"]), email="boss@harness.test", role="admin")
    assert response.status_code == 200 and not User.objects.filter(email="boss@harness.test").exists()
    create(browser(world.people["super admin"]), email="boss@harness.test", role="admin")
    assert User.objects.get(email="boss@harness.test").role == "admin"


def test_the_people_without_the_permission_cannot_create_anyone(world):
    for role in ("manager", "support agent", "customer owner"):
        assert create(browser(world.people[role])).status_code == 403, role
    assert not User.objects.filter(email="new.agent@harness.test").exists()


def test_changing_a_role_is_audited_and_the_person_is_signed_out_of_the_api(world):
    admin = browser(world.people["admin"])
    agent = world.people["support agent"]
    response = admin.post(reverse("console:staff_user_role", args=[agent.pk]), {"role": "manager"})
    assert "now Manager" in " ".join(notes(admin, response))
    agent.refresh_from_db()
    assert agent.role == "manager" and agent.groups.filter(name="Manager").exists()
    assert AuditEvent.objects.filter(action="account.role_changed", target_id=str(agent.pk)).exists()


def test_nobody_can_change_their_own_role_or_status_and_an_admin_cannot_touch_an_admin(world):
    admin = browser(world.people["admin"])
    me, other_admin = world.people["admin"], world.people["super admin"]
    role, status = ("console:staff_user_role", {"role": "manager"}), ("console:staff_user_status", {"status": "suspended"})
    assert "cannot change your own role" in " ".join(notes(admin, admin.post(reverse(role[0], args=[me.pk]), role[1])))
    assert "your own account" in " ".join(notes(admin, admin.post(reverse(status[0], args=[me.pk]), status[1])))
    for url, data in (role, status):
        assert "Super Admin" in " ".join(notes(admin, admin.post(reverse(url, args=[other_admin.pk]), data)))
    me.refresh_from_db()
    other_admin.refresh_from_db()
    assert (me.role, me.status) == ("admin", "active") and (other_admin.role, other_admin.status) == ("super_admin", "active")


def test_suspending_and_reactivating(world):
    admin = browser(world.people["admin"])
    agent = world.people["support agent"]
    url = reverse("console:staff_user_status", args=[agent.pk])
    admin.post(url, {"status": "suspended", "reason": "Left the company"})
    agent.refresh_from_db()
    assert agent.status == "suspended" and not agent.is_active
    event = AuditEvent.objects.filter(action="account.status_changed", target_id=str(agent.pk)).latest("id")
    assert event.metadata["reason"] == "Left the company"
    assert not HttpClient(HTTP_HOST="localhost").login(email=agent.email, password="Str0ng-Passw0rd!x")
    admin.post(url, {"status": "active", "reason": ""})
    agent.refresh_from_db()
    assert agent.status == "active" and agent.is_active


def test_only_staff_can_be_changed_here_and_a_customer_id_is_not_found(world):
    admin = browser(world.people["admin"])
    customer = world.people["customer owner"]
    assert admin.post(reverse("console:staff_user_role", args=[customer.pk]), {"role": "manager"}).status_code == 404
    assert admin.post(reverse("console:staff_user_status", args=[999999]), {"status": "suspended"}).status_code == 404
    customer.refresh_from_db()
    assert customer.role == "customer"


def test_a_manager_can_look_but_the_change_forms_are_refused(world):
    manager = browser(world.people["manager"])
    agent = world.people["support agent"]
    assert manager.post(reverse("console:staff_user_role", args=[agent.pk]), {"role": "manager"}).status_code == 403
    agent.refresh_from_db()
    assert agent.role == "support_agent"
    suspended = manager.post(reverse("console:staff_user_status", args=[agent.pk]), {"status": "suspended", "reason": "x"})
    assert suspended.status_code == 302  # managers may suspend


# --- The email provider -----------------------------------------------------------------------------------------------

PROVIDER = {"name": "Main SMTP", "host": "smtp.harness.test", "port": 587, "username": "mailer", "password": "s3cret-Pass!",
            "use_tls": "on", "timeout": 20, "from_email": "Web Host Era <billing@harness.test>", "is_active": "on"}


def save_provider(client, **extra):
    return client.post(reverse("console:email_provider_save"), {**PROVIDER, **extra})


def test_only_people_with_provider_rights_can_open_the_email_screen(world):
    url = reverse("console:email_providers")
    for role in ("customer owner", "support agent", "manager"):
        assert browser(world.people[role]).get(url).status_code == 403, role
    assert browser(world.people["admin"]).get(url).status_code == 200


def test_the_password_is_encrypted_never_shown_and_never_logged(world):
    admin = browser(world.people["admin"])
    assert save_provider(admin).status_code == 302
    provider = EmailProvider.objects.get()
    assert provider.password_encrypted and "s3cret-Pass!" not in provider.password_encrypted
    assert crypto.decrypt(provider.password_encrypted) == "s3cret-Pass!" and provider.is_active
    for page in (admin.get(reverse("console:email_providers")), admin.get(reverse("console:email_providers"), {"edit": provider.pk}),
                 admin.get(reverse("console:audit_log"))):
        assert b"s3cret-Pass!" not in page.content and provider.password_encrypted.encode() not in page.content
    for event in AuditEvent.objects.filter(action__startswith="email_provider"):
        assert "s3cret" not in str(event.metadata) and event.metadata.get("password_changed") in (True, False, None)


def test_a_blank_password_keeps_the_stored_one_and_a_new_one_replaces_it(world):
    admin = browser(world.people["admin"])
    save_provider(admin)
    provider = EmailProvider.objects.get()
    admin.post(reverse("console:email_provider_save"), {**PROVIDER, "id": provider.pk, "password": "", "name": "Renamed"})
    provider.refresh_from_db()
    assert provider.name == "Renamed" and provider.get_password() == "s3cret-Pass!"
    admin.post(reverse("console:email_provider_save"), {**PROVIDER, "id": provider.pk, "password": "brand-new-1"})
    provider.refresh_from_db()
    assert provider.get_password() == "brand-new-1"
    event = AuditEvent.objects.filter(action="email_provider.updated").latest("id")
    assert event.metadata["password_changed"] is True


def test_only_one_provider_is_in_use_at_a_time(world):
    admin = browser(world.people["admin"])
    save_provider(admin)
    save_provider(admin, name="Backup SMTP", host="smtp2.harness.test")
    active = EmailProvider.objects.filter(is_active=True)
    assert active.count() == 1 and active.get().name == "Backup SMTP"
    save_provider(admin, name="Idle SMTP", host="smtp3.harness.test", is_active="")
    assert EmailProvider.objects.filter(is_active=True).get().name == "Backup SMTP"


@pytest.mark.parametrize("extra,fragment", [({"use_ssl": "on"}, "not both"), ({"port": 0}, "greater than or equal to 1"),
                                            ({"host": ""}, "required"), ({"from_email": ""}, "required")])
def test_a_bad_provider_is_refused_with_a_reason_and_nothing_is_saved(world, extra, fragment):
    response = save_provider(browser(world.people["admin"]), **extra)
    assert response.status_code == 200 and fragment in response.content.decode() and not EmailProvider.objects.exists()


def test_the_active_provider_cannot_be_deleted_but_an_idle_one_can(world):
    admin = browser(world.people["admin"])
    save_provider(admin)
    save_provider(admin, name="Idle", host="smtp9.harness.test", is_active="")
    active, idle = EmailProvider.objects.get(is_active=True), EmailProvider.objects.get(name="Idle")
    response = admin.post(reverse("console:email_provider_delete", args=[active.pk]))
    assert "Switch to another provider" in " ".join(notes(admin, response)) and EmailProvider.objects.filter(pk=active.pk).exists()
    admin.post(reverse("console:email_provider_delete", args=[idle.pk]))
    assert not EmailProvider.objects.filter(pk=idle.pk).exists()


def test_a_test_email_reports_success_and_failure_and_never_echoes_the_password(world, monkeypatch):
    admin = browser(world.people["admin"])
    save_provider(admin)
    provider = EmailProvider.objects.get()
    url = reverse("console:email_provider_test", args=[provider.pk])
    monkeypatch.setattr(EmailMultiAlternatives, "send", lambda self, fail_silently=False: 1)
    ok = admin.post(url, {"to_email": "me@harness.test"})
    assert "Sent." in " ".join(notes(admin, ok))

    def boom(self, fail_silently=False):
        raise ConnectionRefusedError("could not log in with s3cret-Pass! at smtp.harness.test")

    monkeypatch.setattr(EmailMultiAlternatives, "send", boom)
    failed = admin.post(url, {"to_email": "me@harness.test"})
    message = " ".join(notes(admin, failed))
    assert "ConnectionRefusedError" in message and "s3cret-Pass!" not in message
    assert AuditEvent.objects.filter(action="email_provider.tested").count() == 2


def test_a_test_needs_a_real_address_and_the_permission(world):
    admin = browser(world.people["admin"])
    save_provider(admin)
    provider = EmailProvider.objects.get()
    url = reverse("console:email_provider_test", args=[provider.pk])
    assert "valid email" in " ".join(notes(admin, admin.post(url, {"to_email": "nope"})))
    assert browser(world.people["manager"]).post(url, {"to_email": "me@harness.test"}).status_code == 403
    assert not AuditEvent.objects.filter(action="email_provider.tested").exists()


def test_the_health_widget_turns_green_once_a_provider_is_in_use(world):
    admin = browser(world.people["admin"])
    save_provider(admin)
    checks = {c["name"]: c for c in admin.get(reverse("console:widget", args=["health"])).context["checks"]}
    assert checks["Email provider"]["ok"] and "Main SMTP" in checks["Email provider"]["detail"]


# --- The registrar ----------------------------------------------------------------------------------------------------

REGISTRAR = {"name": "Manual desk", "kind": "manual", "sandbox": "on", "credentials": '{"api_key": "reg-secret-key"}',
             "is_active": "on"}


def save_registrar(client, **extra):
    return client.post(reverse("console:registrar_save"), {**REGISTRAR, **extra})


def test_only_people_with_provider_rights_can_open_the_registrar_screen(world):
    url = reverse("console:registrars")
    for role in ("customer owner", "support agent", "manager"):
        assert browser(world.people[role]).get(url).status_code == 403, role
    assert browser(world.people["admin"]).get(url).status_code == 200


def test_registrar_credentials_are_encrypted_and_never_shown(world):
    admin = browser(world.people["admin"])
    assert save_registrar(admin).status_code == 302
    provider = RegistrarProvider.objects.get(name="Manual desk")
    assert "reg-secret-key" not in provider.credentials_encrypted and provider.get_credentials() == {"api_key": "reg-secret-key"}
    for page in (admin.get(reverse("console:registrars")), admin.get(reverse("console:registrars"), {"edit": provider.pk}),
                 admin.get(reverse("console:audit_log"))):
        assert b"reg-secret-key" not in page.content
    admin.post(reverse("console:registrar_save"), {**REGISTRAR, "id": provider.pk, "credentials": ""})  # blank keeps
    assert RegistrarProvider.objects.get(name="Manual desk").get_credentials() == {"api_key": "reg-secret-key"}


@pytest.mark.parametrize("credentials,fragment", [("{not json", "valid JSON"), ("[1, 2]", "JSON object"), ('"text"', "JSON object")])
def test_bad_registrar_credentials_are_refused(world, credentials, fragment):
    before = RegistrarProvider.objects.count()
    response = save_registrar(browser(world.people["admin"]), name="Bad one", credentials=credentials)
    assert response.status_code == 200 and fragment in response.content.decode() and RegistrarProvider.objects.count() == before


def test_only_one_registrar_is_in_use_and_a_used_or_active_one_cannot_be_deleted(world):
    admin = browser(world.people["admin"])
    save_registrar(admin, name="Second", is_active="on")
    assert RegistrarProvider.objects.filter(is_active=True).get().name == "Second"
    old = RegistrarProvider.objects.exclude(name="Second").first()
    if old is not None:
        Domain.objects.filter(pk=world.objects["domains"]["active"].pk).update(registrar=old)
        assert "cannot be deleted" in " ".join(notes(admin, admin.post(reverse("console:registrar_delete", args=[old.pk]))))
        assert RegistrarProvider.objects.filter(pk=old.pk).exists()
    active = RegistrarProvider.objects.get(is_active=True)
    assert "Switch to another registrar" in " ".join(notes(admin, admin.post(reverse("console:registrar_delete", args=[active.pk]))))


def test_the_manual_registrar_is_the_only_kind_offered_today(world):
    page = browser(world.people["admin"]).get(reverse("console:registrars")).content.decode()
    assert "Manual (no real registration" in page and page.count("<option") == 1


def test_registrar_changes_are_audited_without_secrets(world):
    admin = browser(world.people["admin"])
    save_registrar(admin, name="Audited desk")
    event = AuditEvent.objects.filter(action="registrar_provider.created").latest("id")
    assert event.metadata["credentials_changed"] is True and "reg-secret-key" not in str(event.metadata)


# --- The menu ---------------------------------------------------------------------------------------------------------

def test_the_setup_menu_offers_these_screens_only_to_who_may_open_them(world):
    from django.test import RequestFactory

    from apps.core import navigation

    def labels(role):
        request = RequestFactory().get("/staff/")
        request.user = world.people[role]
        request.resolver_match = None
        menus = navigation.build(request, "staff")
        return {c.label for e in menus["main"] for c in (e.children or [e])}

    assert {"Users", "Email provider", "Domain registrar"} <= labels("admin")
    assert "Users" in labels("manager") and "Email provider" not in labels("manager")
    assert not {"Users", "Email provider", "Domain registrar"} & labels("support agent")
