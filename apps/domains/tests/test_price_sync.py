"""Keeping the TLD price list in step with the company website's price table."""
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.core.exceptions import ServiceError
from apps.domains import price_sync
from apps.domains.models import TldPricing

pytestmark = pytest.mark.django_db

ROWS = [(".com", 1, "4849.00"), (".net", 1, "4950.00"), (".org", 1, "4350.50"), (".biz", 1, "6900.00"), (".info", 1, "6399.50"), (".us", 1, "3999.00"),
        (".uk", 1, "5890.00"), (".co.uk", 1, "5890.00"), (".tv", 1, "13980.00"), (".com.pk", 2, "4200.00"), (".pk", 2, "4200.00"), (".xyz", 1, "3590.00")]


def page(rows=ROWS, code="PKR", extra=""):
    body = "".join(f"<tr><td>{t}</td><td>{y}</td><td>{code}{p}</td><td>{code}{p}</td><td>{code}{p}</td></tr>" for t, y, p in rows)
    return ('<html><body><table class="other"><tr><th>Plan</th><th>Price</th></tr><tr><td>x</td><td>1</td></tr></table>'
            '<table id="tablepress-domains"><thead><tr><th>TLD</th><th>Min. Years </th><th>Register </th><th>Transfer </th><th>Renew <br></th></tr></thead>'
            f"<tbody>{body}{extra}</tbody></table></body></html>")


@pytest.fixture(autouse=True)
def pkr(settings):
    settings.STORE_CURRENCY = "PKR"


def test_the_table_is_read_and_junk_rows_are_left_out():
    rows, currency = price_sync.parse(page(extra="<tr><td>not a tld</td><td>1</td><td>PKR1.00</td><td>PKR1.00</td><td>PKR1.00</td></tr>"
                                                  "<tr><td>.bad</td><td>1</td><td>free</td><td>x</td><td>y</td></tr>"
                                                  "<tr><td>.zero</td><td>1</td><td>PKR0.00</td><td>PKR0.00</td><td>PKR0.00</td></tr>"))
    assert currency == "PKR" and [r["tld"] for r in rows] == [r[0] for r in ROWS]
    com = rows[0]
    assert com == {"tld": ".com", "min_years": 1, "register": Decimal("4849.00"), "transfer": Decimal("4849.00"), "renew": Decimal("4849.00")}
    assert next(r for r in rows if r["tld"] == ".pk")["min_years"] == 2


def test_a_page_without_a_price_table_gives_nothing():
    assert price_sync.parse("<html><body><p>hello</p></body></html>") == ([], "")


def test_prices_are_added_then_left_alone_when_nothing_changed():
    first = price_sync.sync(html=page())
    assert len(first["created"]) == len(ROWS) and not first["updated"]
    assert TldPricing.objects.get(tld=".org").register_price == Decimal("4350.50")
    again = price_sync.sync(html=page())
    assert not again["created"] and not again["updated"] and again["unchanged"] == len(ROWS)


def test_a_changed_price_updates_but_keeps_what_the_site_does_not_say():
    price_sync.sync(html=page())
    TldPricing.objects.filter(tld=".com").update(max_years=5, redemption_price=Decimal("9000"), is_active=False)
    changed = [(t, y, "5100.00" if t == ".com" else p) for t, y, p in ROWS]
    report = price_sync.sync(html=page(changed))
    assert report["updated"] == [{"tld": ".com", "old": "4849.00", "new": "5100.00"}]
    row = TldPricing.objects.get(tld=".com")
    assert row.register_price == Decimal("5100.00") and row.max_years == 5 and row.redemption_price == Decimal("9000") and row.is_active is False


def test_an_extension_that_is_not_on_the_page_is_never_removed_or_switched_off():
    TldPricing.objects.create(tld=".shop", register_price=1000, renew_price=1000, transfer_price=1000)
    price_sync.sync(html=page())
    assert TldPricing.objects.get(tld=".shop").is_active is True


def test_a_price_that_moves_a_lot_is_held_back_unless_forced():
    price_sync.sync(html=page())
    wild = [(t, y, "48490.00" if t == ".com" else p) for t, y, p in ROWS]
    held = price_sync.sync(html=page(wild))
    assert held["held"][0]["tld"] == ".com" and TldPricing.objects.get(tld=".com").register_price == Decimal("4849.00")
    forced = price_sync.sync(html=page(wild), force=True)
    assert not forced["held"] and TldPricing.objects.get(tld=".com").register_price == Decimal("48490.00")


def test_a_different_currency_changes_nothing(settings):
    settings.STORE_CURRENCY = "USD"
    with pytest.raises(ServiceError) as err:
        price_sync.sync(html=page())
    assert err.value.code == "currency_mismatch" and "PKR" in err.value.message and not TldPricing.objects.exists()


def test_a_page_that_changed_shape_changes_nothing():
    with pytest.raises(ServiceError) as err:
        price_sync.sync(html=page(ROWS[:3]))
    assert err.value.code == "unexpected_page" and not TldPricing.objects.exists()


def test_only_a_public_https_address_is_read(monkeypatch):
    with pytest.raises(ServiceError) as err:
        price_sync.fetch("http://example.com/prices/")
    assert err.value.code == "bad_source"
    monkeypatch.setattr(price_sync, "_public", lambda host: False)
    with pytest.raises(ServiceError):
        price_sync.fetch("https://internal.example/prices/")


def test_a_dry_run_changes_nothing():
    report = price_sync.sync(html=page(), dry_run=True)
    assert len(report["created"]) == len(ROWS) and not TldPricing.objects.exists()


def test_the_command_reports_what_it_did(monkeypatch, capsys):
    monkeypatch.setattr(price_sync, "fetch", lambda url=None: page())
    call_command("sync_tld_prices")
    out = capsys.readouterr().out
    assert f"{len(ROWS)} added" in out and TldPricing.objects.count() == len(ROWS)


def test_the_scheduled_run_records_a_failure_and_the_screen_shows_it(monkeypatch, staff):
    from apps.accounts.roles import Role

    def boom(url=None):
        raise ServiceError("The price list could not be read (ConnectTimeout).", code="unreachable")

    monkeypatch.setattr(price_sync, "fetch", boom)
    from apps.domains.tasks import sync_tld_prices_task

    assert sync_tld_prices_task()["ok"] is False
    last = price_sync.last_run()
    assert last["ok"] is False and "ConnectTimeout" in last["summary"]
    from django.test import Client

    manager = staff(Role.MANAGER)
    client = Client()
    client.force_login(manager)
    html = client.get(reverse("domains_staff:tld_list")).content.decode()
    assert "failed" in html and "Read prices now" in html
    monkeypatch.setattr(price_sync, "fetch", lambda url=None: page())
    reply = client.post(reverse("domains_staff:tld_sync"), follow=True)
    assert "Prices read" in reply.content.decode() and TldPricing.objects.count() == len(ROWS)
    assert price_sync.last_run()["ok"] is True


def test_a_moved_page_is_followed_and_checked_again(monkeypatch):
    class Reply:
        def __init__(self, status, location="", body=b""):
            self.status_code, self.headers = status, ({"Location": location} if location else {})
            self.is_redirect = bool(location)
            import io

            stream = io.BytesIO(body)
            self.raw = type("Raw", (), {"read": staticmethod(lambda n, decode_content=True: stream.read(n))})()

        def close(self):
            pass

    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if url.endswith("/domain-pricing/"):
            return Reply(301, "/domain-pricing-pakistan/")
        return Reply(200, body=page().encode())

    monkeypatch.setattr(price_sync.requests, "get", fake_get)
    monkeypatch.setattr(price_sync, "_public", lambda host: True)
    assert "tablepress-domains" in price_sync.fetch("https://example.com/domain-pricing/")
    assert calls == ["https://example.com/domain-pricing/", "https://example.com/domain-pricing-pakistan/"]
    # a redirect to a non-https address is refused
    monkeypatch.setattr(price_sync.requests, "get", lambda url, **kw: Reply(302, "http://evil.example/x"))
    with pytest.raises(ServiceError) as err:
        price_sync.fetch("https://example.com/domain-pricing/")
    assert err.value.code == "bad_source"
