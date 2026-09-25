"""Billing configuration: payment methods, tax rules and coupons (services, API and staff pages)."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.billing import services
from apps.billing.models import Coupon, PaymentMethod, TaxRule
from apps.core.exceptions import ServiceError

pytestmark = pytest.mark.django_db

D = Decimal


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


# --- Model rules ---------------------------------------------------------------------------------

def test_coupon_code_is_stored_upper_case_and_trimmed(manager):
    assert services.save_coupon(manager, "  spring25 ", {"discount_type": "percent", "value": D("25")}).code == "SPRING25"


@pytest.mark.parametrize("kind,value,subtotal,expected", [
    ("percent", "10", "99.99", "10.00"),     # 9.999 -> 10.00, half-up
    ("percent", "12.5", "10.00", "1.25"),
    ("percent", "100", "40.00", "40.00"),
    ("fixed", "7.50", "40.00", "7.50"),
    ("fixed", "70", "40.00", "40.00"),        # never more than the subtotal
])
def test_coupon_amount(manager, kind, value, subtotal, expected):
    coupon = services.save_coupon(manager, "C", {"discount_type": kind, "value": D(value)})
    assert coupon.amount_for(D(subtotal)) == D(expected)


def test_coupon_window_messages(manager):
    now = timezone.now()
    expired = services.save_coupon(manager, "OLD", {"discount_type": "fixed", "value": D("1"),
                                                    "valid_until": now - timedelta(days=1)})
    with pytest.raises(ValueError, match="expired"):
        expired.check_window()
    future = services.save_coupon(manager, "NEW", {"discount_type": "fixed", "value": D("1"),
                                                   "valid_from": now + timedelta(days=1)})
    with pytest.raises(ValueError, match="not active yet"):
        future.check_window()


def test_tax_rate_and_country_are_validated(manager):
    with pytest.raises(ValidationError):
        services.save_tax_rule(manager, "US", name="Bad", rate=D("101"))
    with pytest.raises(ValidationError):
        services.save_tax_rule(manager, "USA", name="Bad", rate=D("5"))
    assert TaxRule.objects.count() == 0


# --- Services --------------------------------------------------------------------------------------

def test_saving_a_payment_method_twice_updates_it(manager):
    first = services.save_payment_method(manager, "bank", name="Bank", instructions="a", sort_order=2)
    second = services.save_payment_method(manager, "bank", name="Bank transfer", instructions="b", sort_order=1)
    assert first.pk == second.pk and PaymentMethod.objects.count() == 1
    assert (second.name, second.instructions, second.sort_order) == ("Bank transfer", "b", 1)
    actions = list(AuditEvent.objects.filter(action__startswith="payment_method").values_list("action", flat=True))
    assert sorted(actions) == ["payment_method.created", "payment_method.updated"]


def test_tax_rules_are_one_per_country_and_upsert(manager):
    services.save_tax_rule(manager, "pk", name="GST", rate=D("17"))
    updated = services.save_tax_rule(manager, "PK", name="GST", rate=D("18"))
    assert TaxRule.objects.filter(country="PK").count() == 1 and updated.rate == D("18")


def test_tax_rule_lookup_prefers_the_country_then_the_default(manager):
    services.save_tax_rule(manager, "", name="Default", rate=D("5"))
    services.save_tax_rule(manager, "GB", name="VAT", rate=D("20"))
    assert services.tax_rule_for_country("gb").name == "VAT"
    assert services.tax_rule_for_country("FR").name == "Default"
    assert services.tax_rule_for_country("").name == "Default"
    assert services.tax_rule_for_country(None).name == "Default"


def test_no_tax_rules_means_no_tax(manager):
    assert services.tax_rule_for_country("US") is None


def test_toggles_are_audited_once_per_change(manager):
    coupon = services.save_coupon(manager, "T", {"discount_type": "fixed", "value": D("1")})
    services.set_coupon_active(manager, coupon, False)
    services.set_coupon_active(manager, coupon, False)  # no-op, no second event
    assert AuditEvent.objects.filter(action="coupon.status_changed").count() == 1


def test_every_billing_write_needs_manage_billing(customer, staff):
    agent = staff(Role.SUPPORT_AGENT)  # has view_billing but not manage_billing
    for actor in (customer, agent):
        with pytest.raises(ServiceError) as exc:
            services.save_payment_method(actor, "x", name="X")
        assert exc.value.code == "permission_denied"
        with pytest.raises(ServiceError):
            services.save_tax_rule(actor, "US", name="X", rate=D("1"))
        with pytest.raises(ServiceError):
            services.save_coupon(actor, "X", {"discount_type": "fixed", "value": D("1")})


def test_customers_only_see_active_payment_methods(manager, customer):
    active = services.save_payment_method(manager, "bank", name="Bank")
    hidden = services.save_payment_method(manager, "card", name="Card")
    services.set_payment_method_active(manager, hidden, False)
    assert list(services.visible_payment_methods_for_user(customer)) == [active]
    assert set(services.visible_payment_methods_for_user(manager)) == {active, hidden}


# --- API ---------------------------------------------------------------------------------------------

def test_payment_methods_api_is_for_signed_in_users_only(api, customer, manager):
    services.save_payment_method(manager, "bank", name="Bank", instructions="IBAN")
    hidden = services.save_payment_method(manager, "card", name="Card")
    services.set_payment_method_active(manager, hidden, False)

    assert api.get("/api/v1/payment-methods/").status_code == 401  # instructions are not public
    api.force_authenticate(customer)
    body = api.get("/api/v1/payment-methods/").json()
    assert [m["code"] for m in body] == ["bank"] and body[0]["instructions"] == "IBAN"
    assert api.post("/api/v1/payment-methods/", {"code": "x", "name": "X"}).status_code == 403


def test_payment_methods_api_staff_crud(api, manager):
    api.force_authenticate(manager)
    created = api.post("/api/v1/payment-methods/", {"code": "bank", "name": "Bank", "sort_order": 3})
    assert created.status_code == 201 and created.json()["sort_order"] == 3
    patched = api.patch("/api/v1/payment-methods/bank/", {"name": "Bank transfer"})
    assert patched.status_code == 200
    assert PaymentMethod.objects.get(code="bank").name == "Bank transfer"
    assert PaymentMethod.objects.get(code="bank").sort_order == 3  # untouched by the partial update
    off = api.post("/api/v1/payment-methods/bank/status/", {"is_active": False})
    assert off.json()["is_active"] is False


def test_tax_rules_api_requires_billing_permissions(api, customer, staff, manager):
    assert api.get("/api/v1/tax-rules/").status_code == 401
    api.force_authenticate(customer)
    assert api.get("/api/v1/tax-rules/").status_code == 403
    api.force_authenticate(staff(Role.SUPPORT_AGENT))
    assert api.get("/api/v1/tax-rules/").status_code == 200  # view_billing
    assert api.post("/api/v1/tax-rules/", {"country": "US", "name": "T", "rate": "5"}).status_code == 403

    api.force_authenticate(manager)
    created = api.post("/api/v1/tax-rules/", {"country": "US", "name": "Sales tax", "rate": "8.25"})
    assert created.status_code == 201 and created.json()["rate"] == "8.25"
    again = api.post("/api/v1/tax-rules/", {"country": "US", "name": "Sales tax", "rate": "9"})  # upsert
    assert again.status_code == 201 and TaxRule.objects.filter(country="US").count() == 1
    default = api.post("/api/v1/tax-rules/", {"name": "Default", "rate": "5"})
    assert default.status_code == 201 and default.json()["country"] == ""
    assert api.post("/api/v1/tax-rules/", {"country": "XX1", "name": "T", "rate": "5"}).status_code == 400


def test_coupons_api_is_staff_only(api, customer, staff, manager):
    services.save_coupon(manager, "SECRET", {"discount_type": "percent", "value": D("50")})
    assert api.get("/api/v1/coupons/").status_code == 401
    api.force_authenticate(customer)
    assert api.get("/api/v1/coupons/").status_code == 403  # customers never enumerate codes
    assert api.get("/api/v1/coupons/SECRET/").status_code == 403

    api.force_authenticate(manager)
    assert api.get("/api/v1/coupons/").json()["count"] == 1
    created = api.post("/api/v1/coupons/", {"code": "new15", "discount_type": "fixed", "value": "15.00"})
    assert created.status_code == 201 and created.json()["code"] == "NEW15"
    assert api.patch("/api/v1/coupons/new15/", {"value": "20.00"}).status_code == 200
    assert Coupon.objects.get(code="NEW15").value == D("20.00")
    assert api.post("/api/v1/coupons/new15/status/", {"is_active": False}).json()["is_active"] is False
    assert api.post("/api/v1/coupons/", {"code": "bad", "discount_type": "percent", "value": "150"}).status_code == 400


# --- Staff pages ----------------------------------------------------------------------------------------

def test_billing_pages_require_login_and_permission(client, customer):
    assert client.get("/staff/billing/").status_code == 302
    client.force_login(customer)
    for path in ("payment-methods", "tax-rules", "coupons"):
        assert client.get(f"/staff/billing/{path}/").status_code == 403


def test_support_agent_can_view_but_not_change_billing_config(client, staff):
    client.force_login(staff(Role.SUPPORT_AGENT))
    page = client.get("/staff/billing/coupons/")
    assert page.status_code == 200 and b"Add / update a coupon" not in page.content
    assert client.post("/staff/billing/coupons/save/", {"code": "X", "discount_type": "fixed", "value": "1"}
                       ).status_code == 403


def test_staff_manage_configuration_through_the_pages(client, manager):
    client.force_login(manager)
    assert client.get("/staff/billing/").status_code == 302

    client.post("/staff/billing/payment-methods/save/", {"code": "bank", "name": "Bank transfer",
                                                         "instructions": "IBAN 1", "sort_order": 0})
    method = PaymentMethod.objects.get(code="bank")
    assert b"Bank transfer" in client.get("/staff/billing/payment-methods/").content
    client.post(f"/staff/billing/payment-methods/{method.pk}/status/", {})  # unchecked box sends nothing
    method.refresh_from_db()
    assert method.is_active is False

    client.post("/staff/billing/tax-rules/save/", {"country": "pk", "name": "GST", "rate": "17"})
    rule = TaxRule.objects.get(country="PK")
    assert b"GST" in client.get("/staff/billing/tax-rules/").content
    client.post(f"/staff/billing/tax-rules/{rule.pk}/status/", {"is_active": "on"})

    client.post("/staff/billing/coupons/save/", {
        "code": "launch", "discount_type": "percent", "value": "20", "max_redemptions": "50",
        "valid_until": "2099-01-31T23:59", "min_subtotal": "",
    })
    coupon = Coupon.objects.get(code="LAUNCH")
    assert coupon.max_redemptions == 50 and coupon.valid_until.year == 2099 and coupon.min_subtotal == 0
    assert b"LAUNCH" in client.get("/staff/billing/coupons/").content


def test_invalid_input_is_reported_not_saved(client, manager):
    client.force_login(manager)
    response = client.post("/staff/billing/coupons/save/", {"code": "X", "discount_type": "percent", "value": "150"},
                           follow=True)
    assert b"cannot exceed 100" in response.content and not Coupon.objects.exists()


def test_overlong_input_is_a_validation_error_not_a_database_error(manager):
    """Regression: upserts must validate before writing (PostgreSQL rejects over-long values at INSERT)."""
    with pytest.raises(ValidationError):
        services.save_payment_method(manager, "x" * 80, name="Too long")
    with pytest.raises(ValidationError):
        services.save_payment_method(manager, "ok", name="n" * 500)
    assert PaymentMethod.objects.count() == 0
