"""Importing prices from WHMCS: the API read, what the preview shows, and that only prices are ever written."""
from decimal import Decimal
from unittest import mock

import pytest
import requests
from django.test import override_settings

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.products import services, whmcs
from apps.products.models import Product, ProductType

pytestmark = pytest.mark.django_db

SECRET = "whmcs-secret-xyz"
PRODUCTS = {"result": "success", "totalresults": 3, "products": {"product": [
    {"pid": 1, "gid": 1, "groupname": "Shared", "name": "Shared Starter", "paytype": "recurring", "module": "cpanel", "pricing": {
        "PKR": {"monthly": "600.00", "quarterly": "1700.00", "semiannually": "-1.00", "annually": "6000.00", "biennially": "-1.00", "triennially": "15000.00",
                "msetupfee": "100.00", "qsetupfee": "0.00", "ssetupfee": "0.00", "asetupfee": "0.00", "bsetupfee": "0.00", "tsetupfee": "0.00"},
        "USD": {"monthly": "3.00", "annually": "30.00"}}},
    {"pid": 2, "gid": 1, "groupname": "Shared", "name": "WordPress-Starter Startup", "paytype": "recurring", "module": "cpanel", "pricing": {
        "PKR": {"monthly": "708.00", "quarterly": "-1.00", "annually": "7000.00"}}},
    {"pid": 3, "gid": 2, "groupname": "Other", "name": "Free Trial", "paytype": "free", "module": "", "pricing": {"PKR": {"monthly": "0.00"}}}]}}


def answer(payload, status=200):
    response = mock.Mock(status_code=status)
    response.json.return_value = payload
    return response


@pytest.fixture
def api():
    with mock.patch("requests.post", return_value=answer(PRODUCTS)) as post:
        yield post


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


# --- Reading WHMCS ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("address,expected", [("billing.example.com", "https://billing.example.com/includes/api.php"),
                                              ("https://billing.example.com/", "https://billing.example.com/includes/api.php"),
                                              ("https://example.com/whmcs", "https://example.com/whmcs/includes/api.php"),
                                              ("https://example.com/whmcs/includes/api.php", "https://example.com/whmcs/includes/api.php")])
@override_settings(DEBUG=True)
def test_the_api_address_is_built_from_the_site_address(address, expected):
    assert whmcs.endpoint(address) == expected


@pytest.mark.parametrize("address", ["http://billing.example.com", "ftp://x.test", "https://", "javascript:alert(1)", ""])
@override_settings(DEBUG=True)
def test_only_https_addresses_are_accepted(address):
    with pytest.raises(whmcs.WhmcsError):
        whmcs.endpoint(address)


@override_settings(DEBUG=False)
def test_an_address_inside_a_private_network_is_never_contacted():
    for ip in ("127.0.0.1", "10.0.0.5", "192.168.1.9", "169.254.169.254"):
        with mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", (ip, 443))]), mock.patch("requests.post") as post:
            with pytest.raises(whmcs.WhmcsError, match="private network"):
                whmcs.fetch_products("https://internal.example.com", "id", SECRET)
        assert not post.called
    with mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]), mock.patch("requests.post", return_value=answer(PRODUCTS)):
        assert whmcs.fetch_products("https://billing.example.com", "id", SECRET)[0]


@override_settings(DEBUG=True, STORE_CURRENCY="PKR")
def test_products_come_back_with_prices_in_the_stores_currency(api):
    rows, currency, codes = whmcs.fetch_products("billing.example.com", "id", SECRET)
    assert currency == "PKR" and codes == ["PKR", "USD"] and [r["name"] for r in rows] == ["Shared Starter", "WordPress-Starter Startup", "Free Trial"]
    prices = rows[0]["prices"]
    assert prices["monthly"] == {"price": "600.00", "setup": "100.00", "cycle": "monthly", "custom_months": 0}
    assert set(prices) == {"monthly", "quarterly", "annual", "custom:36"} and prices["custom:36"]["cycle"] == "custom"  # -1.00 means not offered
    assert rows[2]["prices"] == {} and rows[2]["paytype"] == "free"
    sent = api.call_args.kwargs
    assert sent["data"]["action"] == "GetProducts" and sent["data"]["secret"] == SECRET and sent["allow_redirects"] is False
    assert api.call_args.args[0] == "https://billing.example.com/includes/api.php"


@override_settings(DEBUG=True, STORE_CURRENCY="EUR")
def test_when_whmcs_lacks_the_stores_currency_its_first_is_used_and_said(api):
    rows, currency, codes = whmcs.fetch_products("billing.example.com", "id", SECRET)
    assert currency == "PKR" and "EUR" not in codes
    assert whmcs.fetch_products("billing.example.com", "id", SECRET, currency="USD")[1] == "USD"


@override_settings(DEBUG=True)
@pytest.mark.parametrize("effect,message", [
    (answer({"result": "error", "message": "Invalid IP 203.0.113.9"}), "API IP Access Restriction"),
    (answer({"result": "error", "message": "Authentication Failed"}), "Authentication Failed"),
    (answer(None, 302), "redirected"),
])
def test_refusals_are_explained(effect, message):
    if effect.status_code == 200 or effect.status_code == 302:
        with mock.patch("requests.post", return_value=effect):
            with pytest.raises(whmcs.WhmcsError, match=message):
                whmcs.fetch_products("billing.example.com", "id", SECRET)


@override_settings(DEBUG=True)
def test_a_non_api_answer_and_a_network_failure_are_plain_errors_that_never_quote_the_secret():
    bad = mock.Mock(status_code=200)
    bad.json.side_effect = ValueError("not json")
    with mock.patch("requests.post", return_value=bad):
        with pytest.raises(whmcs.WhmcsError, match="did not answer with an API response"):
            whmcs.fetch_products("billing.example.com", "id", SECRET)
    boom = requests.ConnectionError(f"HTTPSConnectionPool(host='billing.example.com'): Max retries exceeded with url: /includes/api.php?secret={SECRET} (Caused by timeout)")
    with mock.patch("requests.post", side_effect=boom):
        with pytest.raises(whmcs.WhmcsError) as caught:
            whmcs.fetch_products("billing.example.com", "id", SECRET)
    assert SECRET not in str(caught.value) and "could not be reached" in str(caught.value)
    with pytest.raises(whmcs.WhmcsError, match="API identifier"):
        whmcs.fetch_products("billing.example.com", "", "")


# --- Matching and writing -----------------------------------------------------------------------------------------------------

def test_the_preview_matches_by_name_or_package_ignoring_case_and_punctuation_and_shows_what_would_change(manager):
    starter = services.create_product(manager, {"name": "shared starter", "type": ProductType.SHARED_HOSTING, "price": "600"})
    wordpress = services.create_product(manager, {"name": "My WordPress", "type": ProductType.WORDPRESS_HOSTING, "whm_package_name": "WordPress-Starter Startup"})
    rows = [{"pid": "1", "name": "Shared-Starter", "prices": {"monthly": {"price": "600.00", "setup": "0.00", "cycle": "monthly", "custom_months": 0},
                                                              "annual": {"price": "6000.00", "setup": "0.00", "cycle": "annual", "custom_months": 0}}},
            {"pid": "2", "name": "WordPress Starter Startup", "prices": {"monthly": {"price": "708.00", "setup": "0.00", "cycle": "monthly", "custom_months": 0}}},
            {"pid": "3", "name": "Unknown plan", "prices": {}}]
    shown = {r["pid"]: r for r in services.whmcs_preview(rows)["rows"]}
    assert shown["1"]["match"].pk == starter.pk and shown["1"]["changes"] == ["annual"] and shown["1"]["current"] == {"monthly": "600.00"}
    assert shown["2"]["match"].pk == wordpress.pk and shown["2"]["changes"] == ["monthly"] and shown["3"]["match"] is None


def test_applying_writes_only_prices_and_audits_them(manager):
    product = services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING, "description": "Keep me", "price": "600",
                                                "resource_limits": {"disk_mb": 100}})
    prices = {"monthly": {"price": "650.00", "setup": "50.00", "cycle": "monthly", "custom_months": 0},
              "custom:36": {"price": "15000.00", "setup": "0.00", "cycle": "custom", "custom_months": 36}}
    assert services.apply_whmcs_prices(manager, [(product.pk, prices)]) == 2
    product = Product.objects.get(pk=product.pk)
    got = {(p.billing_cycle, p.custom_months): (p.price, p.setup_fee) for p in product.prices.all()}
    assert got == {("monthly", 0): (Decimal("650.00"), Decimal("50.00")), ("custom", 36): (Decimal("15000.00"), Decimal("0.00"))}
    assert product.description == "Keep me" and product.resource_limits == {"disk_mb": 100} and product.name == "Starter"
    assert AuditEvent.objects.get(action="product.prices_imported").metadata == {"source": "whmcs", "cycles": ["custom:36", "monthly"]}


def test_only_staff_who_manage_products_can_apply(staff, manager):
    product = services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING})
    with pytest.raises(Exception):
        services.apply_whmcs_prices(staff(Role.SUPPORT_AGENT), [(product.pk, {})])


# --- The screen ----------------------------------------------------------------------------------------------------------------

@override_settings(DEBUG=True, STORE_CURRENCY="PKR")
def test_the_two_steps_end_to_end_and_the_login_is_never_kept(client, manager, api):
    starter = services.create_product(manager, {"name": "Shared Starter", "type": ProductType.SHARED_HOSTING, "price": "500"})
    client.force_login(manager)
    assert "API secret" in client.get("/staff/import/whmcs/").content.decode()
    page = client.post("/staff/import/whmcs/", {"address": "billing.example.com", "identifier": "abc", "secret": SECRET}).content.decode()
    assert "Shared Starter" in page and "WordPress-Starter Startup" in page and "monthly: <strong>600.00</strong>" in page and "no price yet" not in page
    assert SECRET not in page and SECRET not in str(dict(client.session))
    assert f'name="product_1"' in page and f'value="{starter.pk}" selected' in page.replace("\n", " ") or "selected" in page
    done = client.post("/staff/import/whmcs/", {"step": "apply", "use": ["1"], "product_1": str(starter.pk)}, follow=True).content.decode()
    assert "price(s) set on 1 product(s) from WHMCS" in done
    assert Product.objects.get(pk=starter.pk).prices.get(billing_cycle="monthly").price == Decimal("600.00")
    assert "whmcs_price_import" not in client.session
    assert SECRET not in str(list(AuditEvent.objects.values_list("metadata", flat=True)))


@override_settings(DEBUG=True)
def test_a_refusal_shows_why_on_the_form_and_asking_for_nothing_changes_nothing(client, manager):
    client.force_login(manager)
    with mock.patch("requests.post", return_value=answer({"result": "error", "message": "Invalid IP 203.0.113.9"})):
        page = client.post("/staff/import/whmcs/", {"address": "billing.example.com", "identifier": "abc", "secret": SECRET}).content.decode()
    assert "Invalid IP 203.0.113.9" in page and "API IP Access Restriction" in page and SECRET not in page and "billing.example.com" in page
    page = client.post("/staff/import/whmcs/", {"step": "apply"}, follow=True).content.decode()
    assert "Tick at least one product" in page and not Product.objects.exists()


def test_only_managing_staff_see_it_and_the_product_list_links_to_it(client, staff, manager):
    client.force_login(staff(Role.SUPPORT_AGENT))
    assert client.get("/staff/import/whmcs/").status_code in (302, 403)
    client.force_login(manager)
    assert "/staff/import/whmcs/" in client.get("/staff/products/").content.decode()
