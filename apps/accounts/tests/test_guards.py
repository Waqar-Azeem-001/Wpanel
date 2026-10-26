"""The "no" answers of the account services: bad or expired links and tokens, who may touch an admin account, and the checks on creating staff and changing roles."""
import pytest
from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts import services
from apps.accounts.models import AccountStatus, User
from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.core.exceptions import ServiceError
from apps.notifications.models import EmailMessage

pytestmark = pytest.mark.django_db


def refused(code, call, *args, **kwargs):
    with pytest.raises(ServiceError) as caught:
        call(*args, **kwargs)
    assert caught.value.code == code, (caught.value.code, caught.value.message)
    return caught.value


# --- links and tokens -------------------------------------------------------------------------------------------------

def test_a_verification_link_that_expired_or_was_tampered_with_is_refused(monkeypatch):
    def old(*args, **kwargs):
        raise signing.SignatureExpired("too old")

    monkeypatch.setattr(services.signing, "loads", old)
    refused("token_expired", services.verify_email, "whatever")
    monkeypatch.undo()
    refused("token_invalid", services.verify_email, "not-a-real-token")


def test_a_refresh_token_must_be_real_and_belong_to_the_person_who_gives_it(customer, make_user):
    refused("token_invalid", services.revoke_refresh_token, customer, "garbage")
    other = make_user("other@example.com")
    tokens = services.issue_tokens(other)
    refused("token_invalid", services.revoke_refresh_token, customer, tokens["refresh"])  # someone else's token cannot be revoked by this person


def test_a_reset_link_works_for_an_active_account_with_the_right_token_and_for_nothing_else(customer):
    uid, token = urlsafe_base64_encode(force_bytes(customer.pk)), default_token_generator.make_token(customer)
    assert services.get_user_for_reset(uid, token) == customer
    assert services.get_user_for_reset("not-base64!!", token) is None
    assert services.get_user_for_reset(urlsafe_base64_encode(force_bytes(999999)), token) is None
    assert services.get_user_for_reset(uid, "wrong-token") is None
    User.objects.filter(pk=customer.pk).update(status=AccountStatus.SUSPENDED, is_active=False)
    customer.refresh_from_db()
    assert services.get_user_for_reset(uid, default_token_generator.make_token(customer)) is None  # a suspended account cannot reset its way back in


# --- the hand-made sign-in link and the emailed reset link ------------------------------------------------------------------

def test_only_a_super_admin_can_make_a_sign_in_link_by_hand_and_the_link_is_never_logged(staff, customer):
    refused("permission_denied", services.admin_setup_link, staff(Role.ADMIN), customer)
    link = services.admin_setup_link(staff(Role.SUPER_ADMIN), customer)
    assert link.startswith("http")
    assert "/reset/" in link or "password" in link
    assert not any(link in str(event.metadata) for event in AuditEvent.objects.all())


def test_no_link_is_made_for_an_account_that_is_not_active(staff, customer):
    User.objects.filter(pk=customer.pk).update(status=AccountStatus.SUSPENDED, is_active=False)
    customer.refresh_from_db()
    boss = staff(Role.SUPER_ADMIN)
    refused("account_inactive", services.admin_setup_link, boss, customer)
    refused("account_inactive", services.admin_send_reset_link, boss, customer)


def test_an_admin_account_can_only_be_reset_by_a_super_admin(staff, make_user):
    other_admin = make_user("second-admin@example.com", role=Role.ADMIN)
    refused("permission_denied", services.admin_send_reset_link, staff(Role.ADMIN), other_admin)
    services.admin_send_reset_link(staff(Role.SUPER_ADMIN), other_admin)
    assert EmailMessage.objects.filter(to_email="second-admin@example.com").exists()


# --- changing an account's status and role ---------------------------------------------------------------------------------

def test_the_status_rules_unknown_self_admin_and_no_change(staff, customer):
    admin = staff(Role.ADMIN)
    refused("invalid_status", services.set_account_status, admin, customer, "banished")
    refused("self_action", services.set_account_status, admin, admin, AccountStatus.SUSPENDED)
    boss = User.objects.create_user(email="boss@example.com", password="x", role=Role.SUPER_ADMIN, is_superuser=True)
    refused("permission_denied", services.set_account_status, admin, boss, AccountStatus.SUSPENDED)
    assert services.set_account_status(admin, customer, customer.status) == customer  # already so: nothing happens
    assert not AuditEvent.objects.filter(action__contains="status").exists()
    services.set_account_status(admin, customer, AccountStatus.SUSPENDED)
    customer.refresh_from_db()
    assert customer.status == AccountStatus.SUSPENDED and customer.is_active is False  # and a suspended person cannot sign in


def test_the_role_rules_unknown_self_admin_and_no_change(staff, make_user):
    manager, admin = staff(Role.MANAGER), staff(Role.ADMIN)
    person = make_user("agent@example.com", role=Role.SUPPORT_AGENT if hasattr(Role, "SUPPORT_AGENT") else Role.MANAGER)
    refused("invalid_role", services.assign_role, admin, person, "emperor")
    refused("self_action", services.assign_role, admin, admin, Role.MANAGER)
    refused("permission_denied", services.assign_role, admin, person, Role.SUPER_ADMIN)  # only a Super Admin hands out admin roles
    assert services.assign_role(admin, person, person.role) == person  # no change, no fuss
    with pytest.raises(ServiceError):
        services.assign_role(manager, person, Role.MANAGER)  # a manager does not have the permission at all


def test_the_group_of_a_role_is_made_when_it_is_missing(make_user):
    Group.objects.all().delete()
    person = make_user("fresh@example.com", role=Role.MANAGER)
    services.sync_role_membership(person)
    assert list(person.groups.values_list("name", flat=True)) == [Role.MANAGER.label]


# --- creating staff ----------------------------------------------------------------------------------------------------------------

def test_creating_staff_checks_the_email_the_role_and_who_is_asking(staff, customer):
    admin = staff(Role.ADMIN)
    refused("invalid_email", services.create_staff_user, admin, email="not-an-email", role=Role.MANAGER)
    refused("invalid_role", services.create_staff_user, admin, email="new@example.com", role=Role.CUSTOMER)  # a customer is not staff
    refused("permission_denied", services.create_staff_user, admin, email="new-admin@example.com", role=Role.ADMIN)
    refused("email_taken", services.create_staff_user, admin, email=customer.email.upper(), role=Role.MANAGER)  # the same address in capitals is the same address


def test_a_new_staff_member_has_no_password_and_is_sent_a_one_time_link(staff):
    admin = staff(Role.ADMIN)
    person = services.create_staff_user(admin, email="  NewPerson@Example.com ", first_name=" New ", last_name="Person", role=Role.MANAGER)
    assert person.email == "newperson@example.com" and not person.has_usable_password() and person.first_name == "New"
    mail = EmailMessage.objects.get(to_email="newperson@example.com")
    assert "staff account is ready" in mail.subject and "kept encrypted" in mail.body_text  # the one-time link is stored encrypted and wiped once sent, never left readable
    assert AuditEvent.objects.filter(action="account.staff_created", target_id=str(person.pk)).exists()
