"""'Create website': WordPress or a custom website set up inside a hosting plan the customer already has."""
import pytest
from django.urls import reverse

from apps.core.test_client_area import browser, owner, stranger  # noqa: F401  (the same people the client-area tests use; `world` comes from conftest)

pytestmark = pytest.mark.django_db
WP = {"type": "wordpress", "title": "Acme Shop", "admin_user": "siteadmin", "admin_email": "owner@gmail.example.com", "admin_password": "Kq7vRt2mXzLp-91"}


@pytest.fixture
def demo_account(world):
    from apps.products.models import Server, ServerKind

    account = world.objects["accounts"]["active"]
    Server.objects.filter(pk=account.server_id).update(kind=ServerKind.DEMO)  # the demo provider can install WordPress (as a sample)
    return account


def test_a_plan_without_a_website_invites_the_customer_to_create_one(world, owner, demo_account):
    client = browser(owner)
    page = client.get(reverse("hosting_customer:detail", args=[demo_account.pk])).content.decode()
    assert "Create your website" in page and reverse("hosting_customer:setup", args=[demo_account.pk]) in page
    listing = client.get(reverse("hosting_customer:list")).content.decode()
    assert "Set up website" not in listing and reverse("hosting_customer:detail", args=[demo_account.pk]) in listing  # the list opens a plan; setting up is inside it


def test_the_type_step_offers_wordpress_and_a_custom_website(world, owner, demo_account):
    response = browser(owner).get(reverse("hosting_customer:setup", args=[demo_account.pk]))
    page = response.content.decode()
    assert response.context["step"] == "type" and "WordPress" in page and "Custom website" in page
    assert response["Cache-Control"] == "no-store"


def test_wordpress_is_created_with_everything_automatic_and_the_password_is_never_kept(world, owner, demo_account):
    from apps.audit.models import AuditEvent
    from apps.hosting.models import WebsiteApp

    client = browser(owner)
    details = client.get(reverse("hosting_customer:setup", args=[demo_account.pk]), {"type": "wordpress"})
    assert details.context["step"] == "details" and len(details.context["form"].initial["admin_password"]) >= 12
    reply = client.post(reverse("hosting_customer:setup", args=[demo_account.pk]), WP)
    assert reply.status_code == 302 and reply["Location"] == reverse("hosting_customer:setup_status", args=[demo_account.pk])
    app = WebsiteApp.objects.get(account=demo_account)
    assert app.kind == "wordpress" and app.state == "ready" and app.admin_url.endswith("/wp-admin/") and app.title == "Acme Shop" and app.admin_user == "siteadmin"
    done = client.get(reverse("hosting_customer:setup_status", args=[demo_account.pk])).content.decode()
    assert "Your website is ready" in done and "Open WordPress admin" in done and WP["admin_password"] not in done
    overview = client.get(reverse("hosting_customer:detail", args=[demo_account.pk])).content.decode()
    assert "WP Admin" in overview and "Create your website" not in overview
    assert "WP Admin" in client.get(reverse("hosting_customer:list")).content.decode()
    assert WP["admin_password"] not in repr([getattr(app, f.name) for f in app._meta.fields])  # not on the record
    assert WP["admin_password"] not in repr(list(AuditEvent.objects.values_list("metadata", flat=True)))  # not in the audit trail
    assert client.get(reverse("hosting_customer:setup", args=[demo_account.pk])).status_code == 302  # already set up: back to the progress page


def test_a_custom_website_needs_no_details(world, owner, demo_account):
    from apps.hosting.models import WebsiteApp

    client = browser(owner)
    reply = client.post(reverse("hosting_customer:setup", args=[demo_account.pk]), {"type": "custom"})
    assert reply.status_code == 302 and WebsiteApp.objects.get(account=demo_account).kind == "custom"
    assert "Create your website" not in client.get(reverse("hosting_customer:detail", args=[demo_account.pk])).content.decode()


def test_wordpress_details_are_checked(world, owner, demo_account):
    from apps.hosting.models import WebsiteApp

    client = browser(owner)
    for bad in ({"admin_password": "short"}, {"admin_user": "a b"}, {"admin_email": "nope"}, {"title": "<script>"}):
        response = client.post(reverse("hosting_customer:setup", args=[demo_account.pk]), {**WP, **bad})
        assert response.status_code == 200 and response.context["form"].errors, bad
    assert not WebsiteApp.objects.filter(account=demo_account).exists()


def test_a_server_that_cannot_install_wordpress_does_not_offer_it(world, owner):
    account = world.objects["accounts"]["active"]  # the plain test server: no installer
    client = browser(owner)
    page = client.get(reverse("hosting_customer:setup", args=[account.pk])).content.decode()
    assert "Not available on your server yet" in page and "?type=wordpress" not in page
    response = client.post(reverse("hosting_customer:setup", args=[account.pk]), WP)
    assert response.status_code == 200 and "cannot be installed" in response.content.decode()


def test_someone_elses_plan_cannot_be_set_up(world, stranger, demo_account):
    client = browser(stranger)
    assert client.get(reverse("hosting_customer:setup", args=[demo_account.pk])).status_code == 404
    assert client.post(reverse("hosting_customer:setup", args=[demo_account.pk]), WP).status_code == 404


def test_the_create_button_goes_straight_to_the_only_plan_waiting(world, owner, demo_account):
    from apps.hosting.models import HostingAccount, HostingStatus

    HostingAccount.objects.exclude(pk=demo_account.pk).filter(client=demo_account.client).update(status=HostingStatus.CANCELLED)
    response = browser(owner).get(reverse("hosting_customer:create"))
    assert response.status_code == 302 and response["Location"] == reverse("hosting_customer:setup", args=[demo_account.pk])
