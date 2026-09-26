"""Phase D4e: every screen is on the components; the compatibility stylesheet is gone."""
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client as HttpClient
from django.urls import reverse

pytestmark = pytest.mark.django_db

ROOT = Path(settings.BASE_DIR)
TEMPLATES = ROOT / "templates"
SKIP = {"emails"}  # email templates have their own inline-styled layout


def page_templates():
    return [p for p in sorted(TEMPLATES.rglob("*.html")) if not SKIP & set(p.parts)]


# Class names that belonged to the old bridge sheet and now have Bootstrap equivalents.
OLD = ("btn secondary", "btn danger", "btn small", "table-wrap", "muted", "num", "stats", "stat", "tabs", "subnav", "cols",
       "row-actions", "actions", "records", "notes", "timeline", "errors", "error")


def class_lists(text):
    for match in re.finditer(r'class="([^"]*)"', text):
        yield match.group(1)


def test_no_template_uses_a_class_name_from_the_deleted_bridge_sheet():
    offenders = []
    for path in page_templates():
        for value in class_lists(path.read_text(encoding="utf-8")):
            cleaned = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", " ", value)
            tokens = cleaned.split()
            if "alert" in tokens and "errors" in tokens:  # the form partial's alert box also carries this word
                tokens.remove("errors")
            if "btn" in tokens and ({"secondary", "danger", "small"} & set(tokens)):
                offenders.append((path.name, value))
            offenders += [(path.name, value) for t in tokens if t in OLD and t not in ("btn secondary", "btn danger", "btn small")]
    assert offenders == []


def test_every_button_is_a_bootstrap_button():
    offenders = []
    for path in page_templates():
        for value in class_lists(path.read_text(encoding="utf-8")):
            tokens = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", " ", value).split()
            if "btn" in tokens and not any(t.startswith("btn-") and t != "btn-sm" and t != "btn-lg" for t in tokens):
                offenders.append((path.name, value))
    assert offenders == []


def test_no_card_holds_its_content_without_a_card_body():
    """Bootstrap pads the body, not the card: a bare card would touch its own edges."""
    structured = re.compile(r'class="[^"]*(card-(body|header|footer)|table-responsive|table-wrap|list-group)')
    bare = []
    for path in page_templates():
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'<(section|div|article)\s+class="(card(?:\s[^"]*)?)"[^>]*>', text):
            if "narrow-card" in match.group(2) or "border-warning" in match.group(2):
                continue
            first = re.match(r"\s*(?:\{%.*?%\}\s*)*<(\w+)([^>]*)>", text[match.end():match.end() + 400], re.S)
            if first is None:
                continue
            if not (structured.search(first.group(2)) or first.group(1) in ("table", "header", "form")):
                bare.append((path.name, match.group(0)[:70], first.group(0)[:60]))
    assert bare == []


def test_the_stylesheets_define_the_components_that_are_still_used():
    css = (ROOT / "static" / "css" / "theme.css").read_text(encoding="utf-8")
    used = set()
    for path in page_templates():
        for value in class_lists(path.read_text(encoding="utf-8")):
            used |= set(re.sub(r"\{%.*?%\}|\{\{.*?\}\}", " ", value).split())
    for component in ("field", "filters", "row-form", "grid-form", "inline-form", "split", "meta", "limits", "addon-row"):
        assert (component not in used) or f".{component}" in css, component


def test_the_deleted_sheet_stays_deleted():
    assert not (ROOT / "static" / "css" / "legacy.css").exists()
    assert not [p for p in page_templates() if "legacy.css" in p.read_text(encoding="utf-8")]


def test_each_module_has_one_strip_of_its_pages_with_the_current_one_marked(world):
    client = HttpClient(HTTP_HOST="localhost")
    client.force_login(world.people["admin"])
    for name in ("billing_staff:invoice_list", "support_staff:tickets", "affiliates_staff:overview", "lifecycle_staff:overview",
                 "notifications_staff:overview", "orders_staff:list", "domains_staff:list", "console:email_providers"):
        page = client.get(reverse(name)).content.decode()
        assert page.count('class="groupnav"') == 1 and 'class="subnav"' not in page and 'class="tabs"' not in page, name
        strip = page.split('class="groupnav"')[1].split("</nav>")[0]
        assert strip.count('aria-current="page"') == 1, name  # exactly one page is marked as the current one
    assert 'class="nav nav-tabs' in client.get(reverse("orders_staff:list")).content.decode()  # its All / Pending / Active filters


def test_priority_is_a_status_badge_everywhere(world):
    client = HttpClient(HTTP_HOST="localhost")
    client.force_login(world.people["admin"])
    ticket = world.objects["tickets"]["open"]
    detail = client.get(reverse("support_staff:ticket", args=[ticket.pk])).content.decode()
    assert "status-badge" in detail and "priority-" not in detail
    assert "priority-" not in client.get(reverse("support_staff:overview")).content.decode()


def test_the_staff_pages_have_no_horizontal_scroll_sources(world):
    """A rough guard (the browser pass measures the real thing): no fixed-width inline styles left behind."""
    client = HttpClient(HTTP_HOST="localhost")
    client.force_login(world.people["admin"])
    for name, args in (("billing_staff:invoice_detail", [world.objects["invoices"]["unpaid"].pk]),
                       ("hosting_staff:detail", [world.objects["accounts"]["active"].pk]),
                       ("orders_staff:list", []), ("clients_staff:list", []), ("catalog_staff:product_list", [])):
        page = client.get(reverse(name, args=args)).content.decode()
        assert "width:" not in page.split("<main")[1], name
