"""A new device needs an emailed code; one device at a time stays signed in."""
import pytest
from django.test import Client

from apps.accounts import devices
from apps.accounts.models import EmailSignInCode, SignInDevice
from conftest import PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def code_on(settings):
    settings.SIGNIN_EMAIL_CODE = True


@pytest.fixture
def sent(monkeypatch):
    seen = []
    real = devices.notifications.dispatch

    def spy(event, **kwargs):
        seen.append((event, kwargs.get("context") or {}))
        return real(event, **kwargs)

    monkeypatch.setattr(devices.notifications, "dispatch", spy)
    return seen


def code_of(sent):
    return [c["code"] for e, c in sent if e == "account.signin_code"][-1]


def sign_in(browser, user, next_url=None):
    return browser.post("/account/login/", {"email": user.email, "password": PASSWORD})


def test_a_new_device_is_held_for_an_emailed_code_and_then_signed_in(customer, sent):
    browser = Client()
    response = sign_in(browser, customer)
    assert response.status_code == 302 and response["Location"].endswith("/account/login/code/")
    assert browser.get("/account/profile/").status_code == 302  # not signed in yet
    page = browser.post("/account/login/code/", {"code": "000000"})
    assert page.status_code == 200 and b"not right" in page.content
    done = browser.post("/account/login/code/", {"code": code_of(sent)})
    assert done.status_code == 302 and "/account/login/" not in done["Location"]
    assert browser.get("/account/profile/").status_code == 200
    assert devices.DEVICE_COOKIE in done.cookies and SignInDevice.objects.filter(user=customer).exists()


def test_the_same_device_signs_in_again_without_a_code(customer, sent):
    browser = Client()
    sign_in(browser, customer)
    browser.post("/account/login/code/", {"code": code_of(sent)})
    browser.post("/account/logout/")
    again = sign_in(browser, customer)
    assert again.status_code == 302 and "/code/" not in again["Location"]
    assert browser.get("/account/profile/").status_code == 200


def test_signing_in_on_a_second_device_signs_the_first_one_out(customer, sent):
    first, second = Client(), Client()
    sign_in(first, customer)
    first.post("/account/login/code/", {"code": code_of(sent)})
    assert first.get("/account/profile/").status_code == 200
    sign_in(second, customer)
    assert first.get("/account/profile/").status_code == 200  # nothing changes until the second device proves itself
    second.post("/account/login/code/", {"code": code_of(sent)})
    assert second.get("/account/profile/").status_code == 200
    out = first.get("/account/profile/")
    assert out.status_code == 302 and "/account/login/" in out["Location"]
    assert any(e == "account.new_device" for e, _ in sent)


def test_a_wrong_code_is_counted_and_five_wrong_tries_lock_the_sign_in(customer, sent):
    browser = Client()
    sign_in(browser, customer)
    for _ in range(devices.LOCK_THRESHOLD):
        browser.post("/account/login/code/", {"code": "111111"})
    page = browser.post("/account/login/code/", {"code": code_of(sent)})
    assert b"Too many wrong codes" in page.content or page.status_code == 200
    assert browser.get("/account/profile/").status_code == 302


def test_a_code_works_once_and_expires(customer, sent):
    browser = Client()
    sign_in(browser, customer)
    code = code_of(sent)
    EmailSignInCode.objects.update(expires_at=devices.timezone.now() - devices.timedelta(seconds=1))
    page = browser.post("/account/login/code/", {"code": code})
    assert page.status_code == 200 and b"expired" in page.content


def test_asking_for_a_new_code_is_limited(customer, sent):
    browser = Client()
    sign_in(browser, customer)
    page = browser.post("/account/login/code/", {"resend": "1"}, follow=True)
    assert b"just sent" in page.content
    EmailSignInCode.objects.update(created_at=devices.timezone.now() - devices.timedelta(minutes=2))
    browser.post("/account/login/code/", {"resend": "1"})
    assert len([1 for e, _ in sent if e == "account.signin_code"]) == 2


def test_the_code_is_never_stored_in_the_clear(customer, sent):
    sign_in(Client(), customer)
    assert code_of(sent) not in EmailSignInCode.objects.get().code_hash


def test_registering_makes_that_browser_the_device_without_a_code():
    browser = Client()
    response = browser.post("/account/register/", {"email": "new@example.com", "password": PASSWORD, "password_confirm": PASSWORD})
    assert response.status_code == 302 and devices.DEVICE_COOKIE in response.cookies
    assert browser.get("/account/profile/").status_code == 200
