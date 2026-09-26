"""Phase D7: the design tokens work in both themes (readable text, readable status colours), components use tokens only,
and the light / dark / system choice exists and is remembered."""
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client as HttpClient
from django.urls import reverse

pytestmark = pytest.mark.django_db

STATIC = Path(settings.BASE_DIR) / "static"
CSS = (STATIC / "css" / "theme.css").read_text(encoding="utf-8")
SHELL = (STATIC / "css" / "shell.css").read_text(encoding="utf-8")


def block(start):
    """The declarations of the first rule that begins with ``start``."""
    begin = CSS.index(start) + len(start)
    return CSS[begin:CSS.index("\n}", begin)]


def tokens(text):
    return dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", text))


LIGHT, DARK = tokens(block(":root {\n  /* Brand")), tokens(block('[data-bs-theme="dark"] {'))


def luminance(hex_colour):
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def mix(front, back, percent):
    f, b = front.lstrip("#"), back.lstrip("#")
    return "#" + "".join(f"{round(int(f[i:i + 2], 16) * percent + int(b[i:i + 2], 16) * (1 - percent)):02x}" for i in (0, 2, 4))


def theme(name):
    values = dict(LIGHT)
    if name == "dark":
        values.update(DARK)
    return values


def tint_percent(values, status):
    return int(re.search(r"color-mix\(in srgb, var\(--%s\) (\d+)%%" % status, values[f"--{status}-bg"]).group(1)) / 100


@pytest.mark.parametrize("name", ["light", "dark"])
def test_text_is_readable_on_every_surface(name):
    v = theme(name)
    for text in ("--text", "--muted"):
        for surface in ("--bg", "--surface", "--surface-2", "--elevated"):
            assert contrast(v[text], v[surface]) >= 4.5, (name, text, surface, contrast(v[text], v[surface]))


@pytest.mark.parametrize("name", ["light", "dark"])
def test_every_status_colour_is_readable_as_text_and_on_its_own_tint(name):
    v = theme(name)
    for status in ("ok", "warn", "danger", "info"):
        colour = v[f"--{status}"]
        assert contrast(colour, v["--surface"]) >= 4.5, (name, status)
        tint = mix(colour, v["--surface"], tint_percent(v, status))
        assert contrast(colour, tint) >= 4.5, (name, status, "on its badge", contrast(colour, tint))


def test_the_primary_button_is_readable_in_both_themes_with_the_default_brand():
    brand = LIGHT["--brand-primary"]
    assert contrast("#ffffff", brand) >= 4.5  # light: white text on the brand colour
    accent = mix(brand, "#ffffff", 0.55)  # dark: the brand colour lightened, with dark text on it
    assert contrast(DARK["--accent-text"], accent) >= 4.5


def test_the_brand_colour_used_as_text_reads_on_white_and_on_its_own_tint():
    """--accent-ink is the brand colour darkened for text (links, active menu items); tested with the default brand and two more."""
    for brand in (LIGHT["--brand-primary"], "#2f6df6", "#2459d6"):
        ink = mix(brand, "#000000", 0.85)
        for background in ("#ffffff", LIGHT["--surface-2"], mix(brand, "#ffffff", 0.09)):
            assert contrast(ink, background) >= 4.5, (brand, ink, background, contrast(ink, background))
    assert re.search(r"--accent-ink: color-mix\(in srgb, var\(--brand-primary\) 85%, #000\)", CSS)
    assert DARK["--accent-ink"] == "var(--accent)"  # in dark the lightened accent is already text-safe (tested above)


def test_the_header_is_the_page_surface_so_it_is_readable_by_the_same_tokens_in_both_themes():
    """The minimal header uses no colours of its own: its names alias the surface tokens, which are tested above."""
    assert LIGHT["--chrome-bg"] == "var(--surface)" and LIGHT["--chrome-fg"] == "var(--text)"
    assert LIGHT["--chrome-muted"] == "var(--muted)" and LIGHT["--chrome-line"] == "var(--border)"
    for key in ("--chrome-bg", "--chrome-fg", "--chrome-muted", "--chrome-line"):
        assert key not in DARK, f"dark must not override {key}: it follows the surface tokens"


@pytest.mark.parametrize("name", ["light", "dark"])
def test_the_muted_text_and_the_borders_keep_their_roles_on_the_minimal_palette(name):
    v = theme(name)
    assert contrast(v["--muted"], v["--surface-2"]) >= 4.5
    assert 1.05 <= contrast(v["--border"], v["--bg"]) <= 2.0  # a hairline: visible, never heavy
    assert contrast(v["--border-strong"], v["--surface"]) >= 1.4  # an input edge can be found


def test_both_themes_define_the_semantic_tokens_the_components_use():
    for name in ("--primary", "--secondary", "--success", "--warning", "--danger", "--info", "--bg", "--surface", "--elevated",
                 "--border", "--text", "--muted", "--ok-bg", "--warn-bg", "--danger-bg", "--info-bg", "--accent-subtle"):
        assert name in LIGHT, name
    for name in ("--bg", "--surface", "--surface-2", "--elevated", "--text", "--muted", "--border", "--ok", "--warn", "--danger",
                 "--info", "--accent", "--accent-text", "--accent-subtle", "--shadow-hover"):
        assert name in DARK, f"dark theme does not define {name}"


def test_the_application_components_use_tokens_not_colours():
    """Everything above the sign-in section of shell.css: no hex or rgb colours except the dim page overlay (a shade of black)."""
    body = SHELL.split("/* --- Sign in")[0]
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b", body) == []
    assert set(re.findall(r"rgba?\(([^)]*)\)", body)) <= {"6, 10, 22, .55", "0, 0, 0, .12", "0, 0, 0, .1", "var(--brand-primary-rgb"}


def test_focus_is_visible_and_motion_can_be_switched_off():
    assert ":focus-visible" in CSS and ".chrome a:focus-visible" in SHELL
    assert "prefers-reduced-motion: reduce" in SHELL


def test_there_are_no_shadows_bigger_than_a_menu_needs():
    for value in re.findall(r"--shadow-(?:hover|modal): ([^;]+);", CSS):
        assert max(int(n) for n in re.findall(r"(?<![\d.])(\d+)px", value)) <= 60  # nothing floats far off the page


# --- The theme choice ------------------------------------------------------------------------------------------------

def test_theme_js_offers_light_dark_and_system_and_remembers_it():
    js = (STATIC / "js" / "theme.js").read_text(encoding="utf-8")
    for needle in ('"wp.theme"', '"light"', '"dark"', '"system"', "prefers-color-scheme", "window.wpTheme"):
        assert needle in js, needle


def test_the_account_menu_offers_the_three_choices_in_the_header(world):
    html = HttpClient(HTTP_HOST="localhost")
    html.force_login(world.people["manager"])
    page = html.get(reverse("console:dashboard")).content.decode()
    for choice in ("light", "dark", "system"):
        assert page.count(f'data-theme-choice="{choice}"') == 1
    assert 'role="group" aria-labelledby="theme-label-top"' in page and 'aria-pressed="false"' in page


def test_the_sign_in_screen_follows_the_saved_theme_too():
    page = HttpClient(HTTP_HOST="localhost").get(reverse("accounts:login")).content.decode()
    assert page.index("js/theme.js") < page.index("css/theme.css")  # applied before anything paints


# --- Forms and widgets (D8b) -----------------------------------------------------------------------------------------------

JS = (STATIC / "js" / "shell.js").read_text(encoding="utf-8")


def test_the_form_styles_cover_fields_switches_errors_and_the_footer_bar():
    for needle in (".canvas .field.form-check", ".form-check-input:checked", ".form-actions", 'aria-invalid="true"', ".canvas .stat-tile::before",
                   'main.page > form[method="get"]'):
        assert needle in SHELL, needle
    assert re.search(r"\.canvas \.field\.form-check \.form-check-input \{[^}]*appearance: none", SHELL)  # a yes/no field is a switch


def test_the_form_footer_bar_is_added_only_to_real_forms_never_to_search_forms_or_one_button_forms():
    for guard in ('method") || "get")', "form.closest(\"table, td, th, li, .dropdown-menu, .modal\")", 'form.querySelector("table")', '".field, .form-control'):
        assert guard in JS, guard
    assert "htmx:afterSwap" in JS  # a form that arrives by htmx gets the same bar


def test_signed_in_pages_carry_the_form_script_and_no_breadcrumb_line(world):
    page = HttpClient(HTTP_HOST="localhost")
    page.force_login(world.people["manager"])
    html = page.get(reverse("clients_staff:create")).content.decode()
    assert "js/shell.js" in html and 'class="field' in html and "crumbs-line" not in html
