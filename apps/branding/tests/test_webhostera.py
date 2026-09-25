"""The Web Host Era brand, plans and domain prices loaded by ``seed_webhostera``, and the accent colour rule."""
import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.branding import services
from apps.branding.management.commands.seed_webhostera import BRAND, PLANS, TLDS
from apps.domains.models import RegistrarProvider, TldPricing
from apps.products.models import BillingCycle, Product, Server

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded(settings):
    settings.STORE_CURRENCY = "PKR"
    call_command("seed_webhostera", verbosity=0)


# --- The seed ---------------------------------------------------------------------------------------------------------

def test_the_brand_is_loaded_and_its_colours_are_readable(seeded):
    brand = services.get()
    assert brand.name == "Web Host Era" and brand.primary == "#2a6af2" and brand.accent == "#c8fc35"
    assert "Web Host Era Hosting Ltd" in brand.footer_text
    assert services.contrast_ratio(brand.primary, "#ffffff") >= 4.5
    assert brand.accent_text == "#14213d"  # light lime carries dark text
    assert brand.has_logo and brand.has_favicon


def test_the_logo_and_favicon_are_real_images_served_safely(seeded, client):
    logo = client.get(reverse("brand_logo"))
    assert logo.status_code == 200 and logo["Content-Type"] == "image/png" and logo["X-Content-Type-Options"] == "nosniff"
    favicon = client.get(reverse("brand_favicon"))
    assert favicon.status_code == 200 and favicon["Content-Type"] == "image/png"


def test_every_plan_is_active_orderable_and_priced(seeded):
    assert Product.objects.count() == len(PLANS) == 8
    server = Server.objects.get()
    for name, _kind, monthly, lines, _limits in PLANS:
        product = Product.objects.get(name=name)
        assert product.status == "active" and product.whm_package_name and list(product.servers.all()) == [server]
        assert [str(p.price) for p in product.prices.filter(billing_cycle=BillingCycle.MONTHLY)] == [f"{monthly}.00"]
        assert product.description.splitlines() == lines and len(lines) >= 8
        product.full_clean()  # the limits and the rest are valid


def test_the_plans_are_the_ones_on_the_site(seeded):
    names = set(Product.objects.values_list("name", flat=True))
    assert names == {"Shared Starter", "Shared Grow", "Shared Digital", "WordPress Hosting", "Business Hosting",
                     "Reseller Basic", "Reseller Improved", "Reseller Maximized"}
    assert Product.objects.get(name="Shared Starter").prices.get().price == 600
    assert Product.objects.get(name="Reseller Basic").type == "reseller_hosting"
    assert Product.objects.get(name="WordPress Hosting").type == "wordpress_hosting"


def test_domain_prices_and_a_registrar_are_loaded(seeded):
    assert TldPricing.objects.count() == len(TLDS) == 35
    com = TldPricing.objects.get(tld=".com")
    assert (com.register_price, com.renew_price, com.transfer_price, com.min_years) == (4849, 4849, 4849, 1)
    assert TldPricing.objects.get(tld=".pk").min_years == 2 and TldPricing.objects.get(tld=".com.pk").min_years == 2
    assert RegistrarProvider.objects.filter(is_active=True).count() == 1


def test_running_it_again_changes_nothing_but_restores_edits(seeded):
    product = Product.objects.get(name="Shared Starter")
    product.description = "edited"
    product.save()
    call_command("seed_webhostera", verbosity=0)
    assert Product.objects.count() == 8 and TldPricing.objects.count() == 35 and Server.objects.count() == 1
    assert Product.objects.get(name="Shared Starter").prices.count() == 1
    assert Product.objects.get(name="Shared Starter").description.startswith("10 GB NVMe storage")


def test_the_brand_only_option_leaves_the_catalogue_alone(settings):
    settings.STORE_CURRENCY = "PKR"
    call_command("seed_webhostera", "--brand", verbosity=0)
    assert services.get().name == "Web Host Era" and Product.objects.count() == 0 and TldPricing.objects.count() == 0


def test_it_warns_when_the_store_currency_is_not_rupees(settings, capsys):
    settings.STORE_CURRENCY = "USD"
    call_command("seed_webhostera", "--brand")
    assert "STORE_CURRENCY is USD" in capsys.readouterr().out


def test_the_seeded_brand_can_be_changed_afterwards_in_setup(seeded, staff):
    from apps.accounts.roles import Role

    services.save_settings(staff(Role.ADMIN), values={"site_name": "Another Name", "primary_color": "#123456"})
    assert services.get().name == "Another Name" and services.get().primary == "#123456"
    assert BRAND["site_name"] == "Web Host Era"  # the seed file itself is untouched


# --- The accent colour ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("colour,text", [("#c8fc35", "#14213d"), ("#ffff00", "#14213d"), ("#0b6b3a", "#ffffff"),
                                          ("#1f7a4d", "#ffffff"), ("#000000", "#ffffff")])
def test_the_accent_carries_white_or_dark_text_whichever_reads(colour, text):
    assert services.readable_text(colour) == text
    assert max(services.contrast_ratio(colour, "#ffffff"), services.contrast_ratio(colour, services.DARK_TEXT)) >= 4.5


@pytest.mark.parametrize("mid_tone", ["#808080", "#7a8a30", "#00aaff"])
def test_a_mid_tone_accent_is_refused(staff, mid_tone):
    from django.core.exceptions import ValidationError

    from apps.accounts.roles import Role

    ratio = max(services.contrast_ratio(mid_tone, "#ffffff"), services.contrast_ratio(mid_tone, services.DARK_TEXT))
    if ratio >= 4.5:
        pytest.skip("this colour happens to read with one of the two inks")
    with pytest.raises(ValidationError) as exc:
        services.save_settings(staff(Role.ADMIN), values={"accent_color": mid_tone})
    assert "mid-tone" in str(exc.value)


def test_a_light_accent_is_accepted_and_reaches_the_stylesheet(staff, client):
    from apps.accounts.roles import Role

    services.save_settings(staff(Role.ADMIN), values={"accent_color": "#c8fc35"})
    css = client.get(reverse("brand_css")).content.decode()
    assert "--brand-accent: #c8fc35;" in css and "--brand-accent-text: #14213d;" in css
