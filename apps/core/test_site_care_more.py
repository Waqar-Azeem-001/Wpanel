"""Website care, the parts that decide things: how a page is fetched (redirects, errors, caching headers), how it is scored, and how the care code talks to the hosting server."""
from types import SimpleNamespace

import pytest
import requests

from apps.core.exceptions import ServiceError
from apps.hosting import care, site_apps
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.capabilities import Capability, CapabilityNotSupported
from apps.hosting.models import SpeedTest, WebsiteApp

pytestmark = pytest.mark.django_db


@pytest.fixture
def account(world):
    from apps.products.models import Server, ServerKind

    account = world.objects["accounts"]["active"]
    Server.objects.filter(pk=account.server_id).update(kind=ServerKind.DEMO)
    return account


# --- fetching a page like a visitor -------------------------------------------------------------------------------------------------

def reply(status=200, headers=None, body=(b"x" * 100,), elapsed=0.05, redirect=False):
    return SimpleNamespace(status_code=status, headers=headers or {}, is_redirect=redirect, elapsed=SimpleNamespace(total_seconds=lambda: elapsed),
                           iter_content=lambda size: iter(body), close=lambda: None)


@pytest.fixture
def public(monkeypatch):
    monkeypatch.setattr(site_apps, "_public", lambda host: host.endswith(".example"))


def serve(monkeypatch, *replies):
    queue = list(replies)
    seen = []

    def get(url, **kwargs):
        seen.append(url)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(requests, "get", get)
    return seen


def test_redirects_are_followed_counted_and_stop_after_the_limit(public, monkeypatch):
    seen = serve(monkeypatch, reply(301, {"Location": "https://www.shop.example/"}, redirect=True), reply(200))
    result = care._fetch("http" + "://shop.example/")
    assert result["redirects"] == 1 and result["final_url"] == "https://www.shop.example/" and result["https"] is True and seen[1] == "https://www.shop.example/"
    seen = serve(monkeypatch, *[reply(302, {"Location": "/again"}, redirect=True)] * 3 + [reply(200)])
    result = care._fetch("https://shop.example/", hop_limit=2)
    assert result["redirects"] == 2 and result["status"] == 302  # it gave up following and reports where it stopped


def test_an_address_that_is_not_public_is_refused_before_any_request(public, monkeypatch):
    seen = serve(monkeypatch, reply(200))
    with pytest.raises(ServiceError) as caught:
        care._fetch("https://192.168.0.5/")
    assert caught.value.code == "not_public" and seen == []


@pytest.mark.parametrize("failure,code", [(requests.exceptions.SSLError("bad certificate"), "ssl_error"), (requests.exceptions.ConnectTimeout("slow"), "unreachable"),
                                          (requests.exceptions.ConnectionError("down"), "unreachable")])
def test_a_secure_connection_that_fails_and_a_site_that_does_not_answer_are_told_apart(public, monkeypatch, failure, code):
    serve(monkeypatch, failure)
    with pytest.raises(ServiceError) as caught:
        care._fetch("https://shop.example/")
    assert caught.value.code == code


@pytest.mark.parametrize("headers,cached", [
    ({"Cache-Control": "max-age=3600"}, True), ({"Cache-Control": "public, s-maxage=600"}, True), ({"Cache-Control": "max-age=10"}, False),
    ({"Cache-Control": "max-age=soon"}, False), ({"Cache-Control": "no-store", "Expires": "Wed, 01 Jan 2031 00:00:00 GMT"}, False),
    ({"Cache-Control": "no-cache", "Expires": "Wed, 01 Jan 2031 00:00:00 GMT"}, False), ({"Expires": "Wed, 01 Jan 2031 00:00:00 GMT"}, True), ({}, False),
])
def test_whether_a_page_may_be_kept_comes_from_its_caching_headers(public, monkeypatch, headers, cached):
    serve(monkeypatch, reply(200, headers))
    assert care._fetch("https://shop.example/")["cached"] is cached


def test_compression_is_read_from_the_header_and_a_huge_page_is_measured_only_up_to_a_cap(public, monkeypatch):
    serve(monkeypatch, reply(200, {"Content-Encoding": "GZIP"}))
    assert care._fetch("https://shop.example/")["compressed"] is True
    serve(monkeypatch, reply(200, body=(b"x" * 3_000_000,) * 4))
    result = care._fetch("https://shop.example/")
    assert 5_000_000 < result["size"] <= 9_000_000 and result["compressed"] is False  # it stopped reading after the cap, not after all 12 MB


def test_the_first_byte_time_is_what_the_server_took_plus_the_redirects_before_it(public, monkeypatch):
    serve(monkeypatch, reply(200, elapsed=0.25))
    assert care._fetch("https://shop.example/")["first_byte_ms"] >= 250


# --- the score ----------------------------------------------------------------------------------------------------------------------------

BEST = {"first_byte_ms": 100, "total_ms": 500, "https": True, "compressed": True, "size": 50_000, "cached": True, "redirects": 0}


@pytest.mark.parametrize("change,lost,words", [
    ({"first_byte_ms": 300}, 8, "a little slowly"), ({"first_byte_ms": 700}, 22, "takes a while"), ({"first_byte_ms": 1500}, 38, "slow to answer"),
    ({"first_byte_ms": 2500}, 55, "more than two seconds"), ({"total_ms": 3500}, 8, "three seconds"), ({"total_ms": 7000}, 15, "six seconds"),
    ({"https": False}, 20, "HTTPS"), ({"compressed": False}, 8, "not compressed"), ({"size": 3_000_000}, 10, "larger than 2 MB"),
    ({"cached": False}, 4, "keep it"), ({"redirects": 3}, 5, "redirects"),
])
def test_each_fault_costs_its_own_points_and_gives_its_own_advice(change, lost, words):
    points, advice = care.score({**BEST, **change})
    assert points == 100 - lost and any(words in line for line in advice) and len(advice) == 1


def test_the_score_never_goes_below_zero_or_above_a_hundred():
    assert care.score(BEST) == (100, [])
    worst = {"first_byte_ms": 5000, "total_ms": 9000, "https": False, "compressed": False, "size": 9_000_000, "cached": False, "redirects": 9}
    assert care.score(worst)[0] == 0  # 100 - 55 - 15 - 20 - 8 - 10 - 4 - 5 would be -17: it stops at zero


# --- the speed test chooses its address and explains failure ----------------------------------------------------------------------

def test_the_secure_address_is_tried_first_and_the_plain_one_if_it_fails(account, monkeypatch):
    tried = []
    ok = {"final_url": "http" + "://acme.example/", "status": 200, "first_byte_ms": 150, "total_ms": 400, "size": 40_000, "redirects": 0, "https": False,
          "compressed": True, "cached": True}

    def fetch(url):
        tried.append(url)
        if url.startswith("https"):
            raise ServiceError("The secure connection to your website failed.", code="ssl_error")
        return ok

    monkeypatch.setattr(care, "_fetch", fetch)
    result = care.speed_test(account)
    assert tried[0].startswith("https") and tried[1].startswith("http:") and result.ok and result.https is False
    assert any("HTTPS" in line for line in result.advice)  # the fallback worked, and it says the site is not served securely


def test_when_neither_address_answers_the_result_says_why_and_what_to_check(account, monkeypatch):
    def fetch(url):
        raise ServiceError("Your website did not answer (ConnectTimeout).", code="unreachable")

    monkeypatch.setattr(care, "_fetch", fetch)
    result = care.speed_test(account)
    assert result.ok is False and "did not answer" in result.error and any("points to your hosting" in line for line in result.advice)
    assert SpeedTest.objects.filter(pk=result.pk).exists()


# --- how the care code talks to the hosting server -------------------------------------------------------------------------------

def test_an_operation_the_care_code_has_no_right_to_ask_for_is_refused(account):
    with pytest.raises(ServiceError) as caught:
        care._Io(None, account).call(Capability.FILES, "format_the_disk")
    assert caught.value.code == "operation_not_allowed"


def test_the_panels_own_job_stops_cleanly_when_the_server_cannot_do_it_or_fails(account, monkeypatch):
    io = care._Io(None, account)
    operation = next(iter(care.OPERATIONS[Capability.FILES]))
    monkeypatch.setattr(care, "get_adapter", lambda server: SimpleNamespace())
    monkeypatch.setattr(care, "supported_capabilities", lambda adapter: set())
    with pytest.raises(ServiceError) as caught:
        io.call(Capability.FILES, operation)
    assert caught.value.code == "capability_not_supported"

    def refuses(username, *args, **kwargs):
        raise CapabilityNotSupported("no")

    monkeypatch.setattr(care, "supported_capabilities", lambda adapter: {Capability.FILES})
    monkeypatch.setattr(care, "get_adapter", lambda server: SimpleNamespace(**{operation: refuses}))
    with pytest.raises(ServiceError) as caught:
        io.call(Capability.FILES, operation)
    assert caught.value.code == "capability_not_supported"

    def breaks(username, *args, **kwargs):
        raise HostingError("the server is busy")

    monkeypatch.setattr(care, "get_adapter", lambda server: SimpleNamespace(**{operation: breaks}))
    with pytest.raises(ServiceError) as caught:
        io.call(Capability.FILES, operation)
    assert caught.value.code == "adapter_error" and "the server is busy" in caught.value.message


def test_the_panels_own_job_passes_the_account_name_and_drops_the_audit_note(account, monkeypatch):
    operation = next(iter(care.OPERATIONS[Capability.FILES]))
    got = {}

    def works(username, *args, **kwargs):
        got.update(username=username, args=args, kwargs=kwargs)
        return "done"

    monkeypatch.setattr(care, "get_adapter", lambda server: SimpleNamespace(**{operation: works}))
    monkeypatch.setattr(care, "supported_capabilities", lambda adapter: {Capability.FILES})
    assert care._Io(None, account).call(Capability.FILES, operation, "a", audit_metadata={"x": 1}, flag=True) == "done"
    assert got == {"username": account.username, "args": ("a",), "kwargs": {"flag": True}}


def test_support_is_false_when_the_server_cannot_even_be_asked(account, monkeypatch):
    def broken(acct):
        raise ServiceError("no server", code="x")

    monkeypatch.setattr(care.control, "capabilities_for", broken)
    assert care._Io(None, account).supports(Capability.FILES) is False


def test_updates_can_only_be_managed_on_a_ready_wordpress_website(account):
    assert care.can_manage_updates(account) is False
    WebsiteApp.objects.create(account=account, kind="custom", state="ready")
    assert care.can_manage_updates(account) is False  # a custom website has nothing to keep up to date
    WebsiteApp.objects.filter(account=account).update(kind="wordpress", state="installing")
    assert care.can_manage_updates(account) is False
    WebsiteApp.objects.filter(account=account).update(state="ready")
    assert care.can_manage_updates(account) is True
    assert care.care_for(account).pk == care.care_for(account).pk  # one care record per website, made on first use
