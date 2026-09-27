"""
Phase 16 (security hardening): two-factor sign-in (TOTP), optional for everyone (staff turn it on themselves from
their profile, the same as a customer); account lockout beyond the API's per-IP rate limit; security response
headers; and the API login carrying the same rules.
"""
import time

import pytest
from django.core import mail
from django.test import Client as HttpClient
from django.urls import reverse

from apps.accounts import mfa
from apps.accounts import services as account_services
from apps.accounts.models import MFARecoveryCode
from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.core.exceptions import ServiceError

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ng-Passw0rd!x"


def browser(user=None):
    client = HttpClient(HTTP_HOST="localhost", raise_request_exception=False)
    if user is not None:
        client.force_login(user)
    return client


@pytest.fixture
def boss(make_user):
    # This file is about two-factor sign-in itself, so its own fixtures start without it, even though the global
    # ``staff`` fixture models an account that already turned it on.
    return make_user("super_admin@example.com", role=Role.SUPER_ADMIN, mfa_enabled=False)


@pytest.fixture
def manager(make_user):
    return make_user("manager@example.com", role=Role.MANAGER, mfa_enabled=False)


def enable_mfa(user):
    """Set two-factor sign-in on for ``user`` the way the setup page does, returning (secret, recovery_codes)."""
    secret, _uri = account_services.mfa_begin_setup(user)
    codes = account_services.mfa_confirm_setup(user, mfa.totp_at(secret, time.time()))
    user.refresh_from_db()
    return secret, codes


def secret_shown_on(page):
    """The secret key from the setup page's own field (not the first `value="..."` in the page, which is the CSRF token)."""
    after = page.split('id="mfa-secret"', 1)[1]
    return after.split('value="', 1)[1].split('"', 1)[0].replace(" ", "")


# --- The TOTP maths itself, no database ------------------------------------------------------------------------------------------

def test_a_code_is_accepted_now_and_within_the_clock_drift_window_but_not_further_out():
    secret = mfa.generate_secret()
    now = 1_700_000_000.0
    code = mfa.totp_at(secret, now)
    assert mfa.verify(secret, code, at=now)
    assert mfa.verify(secret, code, at=now + mfa.PERIOD)  # one step later: still within the window
    assert mfa.verify(secret, code, at=now - mfa.PERIOD)  # one step earlier
    assert not mfa.verify(secret, code, at=now + 3 * mfa.PERIOD)  # too far out
    assert not mfa.verify(secret, "000000", at=now)
    assert not mfa.verify(secret, "12345", at=now)  # too short
    assert not mfa.verify(secret, "abcdef", at=now)  # not digits
    assert not mfa.verify("", code, at=now)


def test_two_secrets_are_different_and_the_formatted_secret_round_trips():
    a, b = mfa.generate_secret(), mfa.generate_secret()
    assert a != b and len(a) >= 32
    assert mfa.formatted(a).replace(" ", "") == a


def test_the_provisioning_uri_names_the_brand_and_the_account():
    secret = mfa.generate_secret()
    uri = mfa.provisioning_uri(secret, email="ada@example.test", issuer="Acme Hosting")
    assert uri.startswith("otpauth://totp/") and "ada%40example.test" in uri and "Acme%20Hosting" in uri and secret in uri


def test_recovery_codes_are_ten_unique_codes_and_normalize_ignores_case_and_dashes():
    codes = mfa.generate_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    one = codes[0]
    assert mfa.normalize_recovery_code(one) == mfa.normalize_recovery_code(one.upper().replace("-", " "))


# --- Setting it up -----------------------------------------------------------------------------------------------------------------

def test_setting_up_two_factor_sign_in_needs_the_right_code_and_gives_ten_recovery_codes(manager):
    secret, _uri = account_services.mfa_begin_setup(manager)
    assert not manager.mfa_enabled  # not active until confirmed
    with pytest.raises(ServiceError) as caught:
        account_services.mfa_confirm_setup(manager, "000000")
    assert caught.value.code == "mfa_invalid_code" and not manager.mfa_enabled
    codes = account_services.mfa_confirm_setup(manager, mfa.totp_at(secret, time.time()))
    manager.refresh_from_db()
    assert manager.mfa_enabled and manager.mfa_confirmed_at is not None
    assert len(codes) == 10 and MFARecoveryCode.objects.filter(user=manager, used_at__isnull=True).count() == 10
    assert AuditEvent.objects.filter(action="account.mfa_enabled", target_id=str(manager.pk)).exists()
    assert any("Two-factor" in m.subject for m in mail.outbox)  # the account was told


def test_cannot_set_up_again_while_already_on(manager):
    enable_mfa(manager)
    with pytest.raises(ServiceError) as caught:
        account_services.mfa_begin_setup(manager)
    assert caught.value.code == "mfa_already_enabled"
    with pytest.raises(ServiceError):
        account_services.mfa_confirm_setup(manager, "123456")


def test_the_setup_page_walks_through_scan_then_confirm(manager):
    client = browser(manager)
    page = client.get(reverse("accounts:mfa_setup")).content.decode()
    assert "Add this account to your authenticator app" in page and "otpauth://totp/" in page
    secret = secret_shown_on(page)
    posted = client.post(reverse("accounts:mfa_setup"), {"code": mfa.totp_at(secret, time.time())})
    assert posted.status_code == 302 and posted.url == reverse("accounts:mfa_recovery_codes")
    codes_page = client.get(posted.url).content.decode()
    assert "recovery-codes" in codes_page


def test_recovery_codes_shown_once_then_gone(manager):
    client = browser(manager)
    page = client.get(reverse("accounts:mfa_setup")).content.decode()
    secret = secret_shown_on(page)
    client.post(reverse("accounts:mfa_setup"), {"code": mfa.totp_at(secret, time.time())})
    first = client.get(reverse("accounts:mfa_recovery_codes")).content.decode()
    assert "recovery-codes" in first
    again = client.get(reverse("accounts:mfa_recovery_codes"))
    assert again.status_code == 302 and again.url == reverse("accounts:mfa_setup")  # nothing left to show


# --- Signing in with it on ---------------------------------------------------------------------------------------------------------

def test_a_right_password_does_not_sign_in_someone_with_two_factor_on(manager):
    enable_mfa(manager)
    client = HttpClient(HTTP_HOST="localhost")
    response = client.post(reverse("accounts:login"), {"email": manager.email, "password": PASSWORD})
    assert response.status_code == 302 and response.url == reverse("accounts:mfa_verify")
    assert not client.session.get("_auth_user_id")  # not signed in yet


def test_the_right_code_signs_in_and_a_wrong_one_does_not(manager):
    secret, _codes = enable_mfa(manager)
    client = HttpClient(HTTP_HOST="localhost")
    client.post(reverse("accounts:login"), {"email": manager.email, "password": PASSWORD})
    bad = client.post(reverse("accounts:mfa_verify"), {"code": "000000"})
    assert bad.status_code == 200 and b"not right" in bad.content and not client.session.get("_auth_user_id")
    good = client.post(reverse("accounts:mfa_verify"), {"code": mfa.totp_at(secret, time.time())})
    assert good.status_code == 302 and good.url == reverse("accounts:profile")
    assert str(client.session.get("_auth_user_id")) == str(manager.pk)


def test_a_recovery_code_signs_in_once_and_then_stops_working(manager):
    _secret, codes = enable_mfa(manager)
    client = HttpClient(HTTP_HOST="localhost")
    client.post(reverse("accounts:login"), {"email": manager.email, "password": PASSWORD})
    ok = client.post(reverse("accounts:mfa_verify"), {"code": codes[0]})
    assert ok.status_code == 302 and str(client.session.get("_auth_user_id")) == str(manager.pk)
    client.post(reverse("accounts:logout"))
    client.post(reverse("accounts:login"), {"email": manager.email, "password": PASSWORD})
    reused = client.post(reverse("accounts:mfa_verify"), {"code": codes[0]})
    assert reused.status_code == 200 and not client.session.get("_auth_user_id")


def test_visiting_the_verify_page_without_a_pending_sign_in_goes_back_to_login():
    response = HttpClient(HTTP_HOST="localhost").get(reverse("accounts:mfa_verify"))
    assert response.status_code == 302 and response.url == reverse("accounts:login")


def test_a_hostile_next_does_not_survive_the_two_factor_detour(manager):
    secret, _codes = enable_mfa(manager)
    client = HttpClient(HTTP_HOST="localhost")
    client.post(reverse("accounts:login") + "?next=https://evil.test/", {"email": manager.email, "password": PASSWORD})
    done = client.post(reverse("accounts:mfa_verify"), {"code": mfa.totp_at(secret, time.time())})
    assert done.status_code == 302 and done.url == reverse("accounts:profile")  # the hostile address was never kept


def test_new_recovery_codes_replace_the_old_ones(manager):
    _secret, old_codes = enable_mfa(manager)
    client = browser(manager)
    saved = client.post(reverse("accounts:mfa_recovery_regenerate"), {"password": PASSWORD})
    assert saved.status_code == 302
    new_page = client.get(reverse("accounts:mfa_recovery_codes")).content.decode()
    assert old_codes[0] not in new_page
    assert MFARecoveryCode.objects.filter(user=manager, used_at__isnull=True).count() == 10
    with pytest.raises(ServiceError):
        account_services.mfa_regenerate_recovery_codes(manager, password="wrong")
    assert account_services.mfa_verify_code_or_recovery(manager, old_codes[0]) is False  # the old code is dead


def test_turning_it_off_needs_the_current_password(manager):
    enable_mfa(manager)
    with pytest.raises(ServiceError):
        account_services.mfa_disable(manager, password="wrong")
    account_services.mfa_disable(manager, password=PASSWORD)
    manager.refresh_from_db()
    assert not manager.mfa_enabled and not MFARecoveryCode.objects.filter(user=manager).exists()
    assert AuditEvent.objects.filter(action="account.mfa_disabled", target_id=str(manager.pk)).exists()


def test_the_disable_page_works_end_to_end(manager):
    enable_mfa(manager)
    client = browser(manager)
    client.post(reverse("accounts:mfa_disable"), {"password": PASSWORD})
    page = client.get(reverse("accounts:mfa_setup")).content.decode()
    assert "Add this account to your authenticator app" in page  # back to the start


# --- A Super Admin can reset someone else's, never their own -----------------------------------------------------------------------

def test_only_a_super_admin_can_reset_someone_elses_two_factor_sign_in(boss, manager):
    enable_mfa(manager)
    enable_mfa(boss)
    with pytest.raises(ServiceError):
        account_services.mfa_admin_reset(manager, manager)  # not a Super Admin
    with pytest.raises(ServiceError) as caught:
        account_services.mfa_admin_reset(boss, boss)  # never yourself, even though boss is a Super Admin with it on
    assert caught.value.code == "self_action" and boss.mfa_enabled
    account_services.mfa_admin_reset(boss, manager)
    manager.refresh_from_db()
    assert not manager.mfa_enabled
    assert AuditEvent.objects.filter(action="account.mfa_reset_by_admin", actor=boss, target_id=str(manager.pk)).exists()


def test_the_reset_button_is_on_the_user_page_only_once_its_set_up(boss, manager):
    boss.mfa_enabled = True  # boss is the one browsing /staff/ here; the test is about manager's two-factor state
    boss.save(update_fields=["mfa_enabled"])
    page = browser(boss).get(reverse("console:user_detail", args=[manager.pk])).content.decode()
    assert "Reset two-factor sign-in" not in page  # nothing to reset yet
    enable_mfa(manager)
    page = browser(boss).get(reverse("console:user_detail", args=[manager.pk])).content.decode()
    assert reverse("console:user_mfa_reset", args=[manager.pk]) in page
    done = browser(boss).post(reverse("console:user_mfa_reset", args=[manager.pk]))
    assert done.status_code == 302
    manager.refresh_from_db()
    assert not manager.mfa_enabled


def test_a_manager_cannot_reset_anyone_elses_two_factor_sign_in(manager, make_user):
    manager.mfa_enabled = True  # manager is the one browsing /staff/ here; the test is about someone else's account
    manager.save(update_fields=["mfa_enabled"])
    other = make_user("agent@example.com", role=Role.SUPPORT_AGENT, mfa_enabled=False)
    enable_mfa(other)
    response = browser(manager).post(reverse("console:user_mfa_reset", args=[other.pk]), follow=True)
    assert "Only a Super Admin" in response.content.decode()  # refused (redirected back with a message), not carried out
    other.refresh_from_db()
    assert other.mfa_enabled


# --- Optional for everyone: staff are never forced into it, but can turn it on the same way a customer can -------------------------

def test_staff_without_it_reach_the_console_normally_the_same_as_a_customer(staff, customer):
    fresh_staff = staff(Role.SUPPORT_AGENT)
    fresh_staff.mfa_enabled = False
    fresh_staff.save(update_fields=["mfa_enabled"])
    assert browser(fresh_staff).get(reverse("console:dashboard")).status_code == 200  # never sent to set it up
    assert browser(customer).get(reverse("dashboard")).status_code in (200, 302)


def test_once_set_up_staff_reach_the_console_normally(staff):
    fresh_staff = staff(Role.MANAGER)
    fresh_staff.mfa_enabled = False
    fresh_staff.save(update_fields=["mfa_enabled"])
    enable_mfa(fresh_staff)
    assert browser(fresh_staff).get(reverse("console:dashboard")).status_code == 200


def test_the_setup_page_is_reachable_to_opt_in_whenever_someone_wants_it(staff):
    fresh_staff = staff(Role.MANAGER)
    fresh_staff.mfa_enabled = False
    fresh_staff.save(update_fields=["mfa_enabled"])
    assert browser(fresh_staff).get(reverse("accounts:mfa_setup")).status_code == 200


# --- Account lockout, beyond the API's per-IP rate limit ----------------------------------------------------------------------------

def test_five_wrong_passwords_lock_the_email_and_the_right_one_no_longer_works_until_it_clears(manager):
    for _ in range(account_services.LOGIN_LOCK_THRESHOLD):
        with pytest.raises(ServiceError):
            account_services.authenticate_user(email=manager.email, password="wrong")
    with pytest.raises(ServiceError) as caught:
        account_services.authenticate_user(email=manager.email, password=PASSWORD)  # even the right one, now
    assert caught.value.code == "account_locked" and caught.value.status_code == 429
    assert AuditEvent.objects.filter(action="auth.account_locked").exists()


def test_a_right_password_before_the_threshold_clears_the_count(manager):
    for _ in range(account_services.LOGIN_LOCK_THRESHOLD - 1):
        with pytest.raises(ServiceError):
            account_services.authenticate_user(email=manager.email, password="wrong")
    account_services.authenticate_user(email=manager.email, password=PASSWORD)
    for _ in range(account_services.LOGIN_LOCK_THRESHOLD - 1):
        with pytest.raises(ServiceError):
            account_services.authenticate_user(email=manager.email, password="wrong")
    account_services.authenticate_user(email=manager.email, password=PASSWORD)  # still works: the earlier count was cleared


def test_an_email_with_no_account_locks_the_same_way_so_nothing_reveals_whether_it_exists():
    for _ in range(account_services.LOGIN_LOCK_THRESHOLD):
        with pytest.raises(ServiceError) as caught:
            account_services.authenticate_user(email="nobody-at-all@example.test", password="whatever")
        assert caught.value.code == "invalid_credentials"
    with pytest.raises(ServiceError) as caught:
        account_services.authenticate_user(email="nobody-at-all@example.test", password="whatever")
    assert caught.value.code == "account_locked"


def test_the_lockout_is_per_account_not_global(manager, staff):
    other = staff(Role.SUPPORT_AGENT)
    for _ in range(account_services.LOGIN_LOCK_THRESHOLD):
        with pytest.raises(ServiceError):
            account_services.authenticate_user(email=manager.email, password="wrong")
    assert account_services.authenticate_user(email=other.email, password=PASSWORD).pk == other.pk


def test_wrong_authenticator_codes_lock_that_account_too(manager):
    enable_mfa(manager)
    for _ in range(account_services.MFA_LOCK_THRESHOLD):
        assert account_services.mfa_verify_code_or_recovery(manager, "000000") is False
    with pytest.raises(ServiceError) as caught:
        account_services.mfa_verify_code_or_recovery(manager, "000000")
    assert caught.value.code == "account_locked"


# --- The API carries the same rules ---------------------------------------------------------------------------------------------

def test_the_api_asks_for_a_code_once_two_factor_sign_in_is_on(api, manager):
    enable_mfa(manager)
    first = api.post("/api/v1/auth/login/", {"email": manager.email, "password": PASSWORD})
    assert first.status_code == 401 and first.data["error"]["code"] == "mfa_required"
    bad = api.post("/api/v1/auth/login/", {"email": manager.email, "password": PASSWORD, "mfa_code": "000000"})
    assert bad.status_code == 401 and bad.data["error"]["code"] == "mfa_invalid_code"


def test_the_api_signs_in_with_a_valid_code_or_a_recovery_code(api, manager):
    secret, codes = enable_mfa(manager)
    ok = api.post("/api/v1/auth/login/", {"email": manager.email, "password": PASSWORD, "mfa_code": mfa.totp_at(secret, time.time())})
    assert ok.status_code == 200 and "access" in ok.data
    ok2 = api.post("/api/v1/auth/login/", {"email": manager.email, "password": PASSWORD, "mfa_code": codes[1]})
    assert ok2.status_code == 200


def test_the_api_login_is_unaffected_without_two_factor_sign_in(api, customer):
    ok = api.post("/api/v1/auth/login/", {"email": customer.email, "password": PASSWORD})
    assert ok.status_code == 200 and "access" in ok.data


# --- Security response headers -----------------------------------------------------------------------------------------------------

def test_every_page_carries_a_strict_content_security_policy_and_a_permissions_policy():
    response = HttpClient(HTTP_HOST="localhost").get(reverse("accounts:login"))
    csp = response["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert response["Permissions-Policy"]


def test_an_uploaded_files_own_stricter_policy_is_not_overwritten(boss):
    from io import BytesIO

    from PIL import Image

    from apps.branding import services as branding

    buffer = BytesIO()
    Image.new("RGB", (16, 16), (10, 20, 30)).save(buffer, "PNG")
    branding.save_settings(boss, favicon=buffer.getvalue())
    response = HttpClient(HTTP_HOST="localhost").get(reverse("brand_favicon"))
    assert response.status_code == 200 and response["Content-Security-Policy"] == "default-src 'none'; sandbox"


def test_no_inline_event_handler_remains_now_that_script_src_has_no_unsafe_inline():
    import re
    from pathlib import Path

    pattern = re.compile(r"\bon(click|submit|change|load|error|input|keyup|keydown|mouseover)=", re.I)
    offenders = [str(p) for p in Path("templates").rglob("*.html") if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


# --- RBAC review (Phase 16 slice 2): route-inventory accuracy, and step-up re-authentication for the most sensitive actions ------------

def test_the_route_inventory_resolves_every_staff_permission_alias_and_names_no_route_bare_public():
    from apps.core.management.commands.route_inventory import collect

    rows = collect()
    staff_rows = [r for r in rows if r[0] == "Staff"]
    assert len(staff_rows) > 50  # a sanity floor: the inventory actually walked the staff URLs
    bare_public = [f"{name} {path}" for area, path, name, view, access in staff_rows if access == "public"]
    assert bare_public == [], "a /staff/ route the inventory could not explain:\n" + "\n".join(bare_public)
    by_name = {name: access for _area, _path, name, _view, access in staff_rows}
    assert "perm: view_users" in by_name["console:staff_users"]  # was "public": portal_permission_required(VIEW_USERS)
    assert "Super Admin only" in by_name["notifications_staff:templates"]  # was "public": a custom decorator, not perm()
    assert "perm: view_support" in by_name["support_staff:departments"]  # was "public": portal_permission_required(VIEW)


def test_permission_aliases_resolves_both_the_wrapped_and_the_bare_alias_style():
    from apps.core.management.commands.route_inventory import permission_aliases

    source = (
        'VIEW = portal_permission_required(perm("view_x"))\n'
        'A, B = perm("view_y"), perm("manage_y")\n'
        'SOLO = perm("view_z")\n'
    )
    aliases = permission_aliases(source)
    assert aliases["VIEW"] == ["view_x"] and aliases["A"] == ["view_y"] and aliases["B"] == ["manage_y"] and aliases["SOLO"] == ["view_z"]
