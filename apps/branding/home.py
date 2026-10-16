"""
The words of the front page, editable in Setup > Home page. Prices, plans, domain extensions and categories are never typed here: the page reads them from the catalog and the price list on
every visit, so changing a price in the backend changes the front page at once. What is typed here is the copy around them (headlines, short paragraphs, the questions and answers).

A text may carry tokens that are filled from live data when the page is drawn, so a sentence can mention a price without ever going stale:
{domain_from} the lowest domain price, {plan_from} the lowest plan price, {currency}, {payment_methods}, {support_email}, {phone}.
"""
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework import status

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from .models import HomeContent

CACHE_KEY = "branding.home.v1"
MAX_FAQ, MAX_WHY, MAX_TEXT = 12, 8, 600
ICONS = (("bi-shield-check", "Shield"), ("bi-arrow-left-right", "Arrows"), ("bi-headset", "Headset"), ("bi-arrow-counterclockwise", "Refund"), ("bi-lightning-charge", "Lightning"),
         ("bi-lock", "Lock"), ("bi-hdd-network", "Server"), ("bi-globe2", "Globe"), ("bi-credit-card", "Card"), ("bi-rocket-takeoff", "Rocket"), ("bi-award", "Award"),
         ("bi-people", "People"), ("bi-clock-history", "Clock"), ("bi-graph-up", "Growth"))

# (key, label, kind, default): kind "line" is one line, "text" is a few lines, "title" is a headline that may use two lines
SECTIONS = [
    {"key": "hero", "title": "First screen", "about": "The headline and search at the very top.", "toggle": None, "fields": [
        ("hero_title", "Headline", "title", "Your idea, online.\nMade easy."),
        ("hero_text", "Short paragraph", "text", "Domains, hosting and support for your business, all in one place. Pick a name, pick a plan and be live today."),
        ("hero_button", "Button", "line", "Get started"),
        ("hero_note", "Line under the button", "line", "7-day refund on hosting"),
        ("search_placeholder", "Search box hint", "line", "Type the domain you want"),
    ]},
    {"key": "plans", "title": "Plans and prices card", "about": "The big card under the first screen, with two small cards beside it.", "toggle": "show_plans_card", "fields": [
        ("plans_chip", "Small label", "line", "Pricing"),
        ("plans_title", "Title", "line", "Plans and prices"),
        ("plans_text", "Text", "text", "Every plan comes with the tools you need on day one: SSL, email, backups and a panel to run it all. See every price, nothing hidden."),
        ("plans_button", "Button", "line", "Explore all offers"),
        ("mini_domains_title", "Domains card title ({domain_from} is the lowest price)", "line", "Domains from {domain_from}"),
        ("mini_domains_text", "Domains card text", "line", "Register the name for your business and manage it from your account."),
        ("mini_move_title", "Second card title", "line", "Free website migration"),
        ("mini_move_text", "Second card text", "line", "Already have a website? We move it for you."),
    ]},
    {"key": "ask", "title": "Share your idea", "about": "A second domain search with popular extensions and their prices.", "toggle": "show_ask", "fields": [
        ("ask_title", "Title", "line", "Share your idea."),
        ("ask_accent", "Coloured end of the title", "line", "We take care of the rest."),
        ("ask_placeholder", "Search box hint", "line", "What is your business called?"),
    ]},
    {"key": "tools", "title": "Tools for every online project", "about": "Four tabs, each leading to categories from your store.", "toggle": "show_tools", "fields": [
        ("tools_title", "Title", "line", "Tools for every online project"),
        ("tools_text", "Text under the title", "text", "A personal page, a business site, an online shop or your own server: start with the right foundation."),
        ("tab1_label", "Tab 1 name", "line", "Websites"), ("tab1_title", "Tab 1 title", "line", "Build it yourself"),
        ("tab1_text", "Tab 1 text", "text", "WordPress or a simple website, installed for you in minutes with SSL switched on. Change it from one dashboard."),
        ("tab2_label", "Tab 2 name", "line", "Business"), ("tab2_title", "Tab 2 title", "line", "A home for your business"),
        ("tab2_text", "Tab 2 text", "text", "More power and room to grow: more websites, more email, more storage, and a team that answers when you ask."),
        ("tab3_label", "Tab 3 name", "line", "Online shop"), ("tab3_title", "Tab 3 title", "line", "Sell online"),
        ("tab3_text", "Tab 3 text", "text", "Hosting built for online shops: fast pages, safe checkouts and the space your products need."),
        ("tab4_label", "Tab 4 name", "line", "Developers"), ("tab4_title", "Tab 4 title", "line", "Your own server"),
        ("tab4_text", "Tab 4 text", "text", "When a website is not enough: a virtual server with full control, or a whole machine of your own."),
    ]},
    {"key": "essentials", "title": "The essentials", "about": "Four coloured cards: hosting, domains, SSL and support.", "toggle": "show_essentials", "fields": [
        ("essentials_title", "Title", "line", "Set up the essentials to go online"),
        ("ess1_title", "Card 1 title", "line", "Hosting"), ("ess1_text", "Card 1 text", "line", "Fast, secure space for your website."),
        ("ess2_title", "Card 2 title", "line", "Domains"), ("ess2_text", "Card 2 text", "line", "The name your customers will type."),
        ("ess3_title", "Card 3 title", "line", "SSL certificates"), ("ess3_text", "Card 3 text", "line", "Show the padlock and keep visitors safe."),
        ("ess4_title", "Card 4 title", "line", "Support"), ("ess4_text", "Card 4 text", "line", "Answers in your account, any time."),
    ]},
    {"key": "choose", "title": "Choose a plan", "about": "Three plans, the cheapest of three different kinds, read from your catalog.", "toggle": "show_choose", "fields": [
        ("choose_title", "Title", "line", "Choose the plan that matches what you need"),
        ("choose_text", "Text under the title", "text", "Start small and move up when your website grows. Every price is on show."),
        ("choose_button", "Button", "line", "See all plans and prices"),
    ]},
    {"key": "power", "title": "More power", "about": "Four dark cards for servers and bigger hosting, from your store categories.", "toggle": "show_power", "fields": [
        ("power_title", "Title", "line", "More power when you need it"),
        ("power_text", "Text under the title", "text", "Servers and hosting for projects that outgrow the basics."),
    ]},
    {"key": "why", "title": "Why us", "about": "Four short promises. Only promise what you do.", "toggle": "show_why", "fields": [
        ("why_title", "Title", "line", "Everything taken care of"),
    ]},
    {"key": "faq", "title": "Questions", "about": "The questions people ask before they buy.", "toggle": "show_faq", "fields": [
        ("faq_title", "Title", "line", "Frequently asked questions"),
    ]},
    {"key": "cta", "title": "Last call", "about": "The blue band at the end of the page.", "toggle": "show_cta", "fields": [
        ("cta_title", "Headline", "title", "Imagined it.\nNow make it real."),
        ("cta_text", "Text", "text", "Find your domain, pick a plan and be online today. We migrate your existing site for free."),
        ("cta_button", "Button", "line", "Get started"),
        ("cta_placeholder", "Search box hint", "line", "Find a domain for your business"),
    ]},
]
TOGGLES = [s["toggle"] for s in SECTIONS if s["toggle"]]
DEFAULT_WHY = [
    {"icon": "bi-shield-check", "title": "Free SSL certificate", "text": "Every website opens with the padlock, so visitors trust it."},
    {"icon": "bi-arrow-left-right", "title": "Free website migration", "text": "Bring your existing website over; we move it for you."},
    {"icon": "bi-headset", "title": "Support through your panel", "text": "Open a ticket from your account, any time, and follow the answer there."},
    {"icon": "bi-arrow-counterclockwise", "title": "7-day refund on hosting", "text": "Try it. If it is not for you, you get your money back."},
]
DEFAULT_FAQ = [
    {"q": "How do I get started?", "a": "Search for the domain you want, pick a plan, and create your account. After you pay, your website is set up from your account, where you can install WordPress with one click."},
    {"q": "Can you move my existing website?", "a": "Yes. Migration is free: open a ticket from your account with the address of your current website and we copy it over and tell you when it is ready to check."},
    {"q": "How can I pay?", "a": "You can pay by {payment_methods}. Prices are shown in {currency}. Your invoice and receipts are in your account."},
    {"q": "Is there a money-back guarantee?", "a": "Hosting comes with a 7-day refund. If it is not what you wanted, tell us in the first week."},
    {"q": "Can I manage everything in one place?", "a": "Yes. Your websites, domains, email, invoices and support tickets are all in your account. Each website has its own dashboard with files, databases, backups and WordPress tools."},
    {"q": "Where can I get help?", "a": "Start with the help centre. If you need a person, open a ticket from your account and our team replies there."},
]


def defaults():
    out = {key: default for section in SECTIONS for key, _label, _kind, default in section["fields"]}
    out.update({toggle: True for toggle in TOGGLES})
    out["why_items"] = [dict(i) for i in DEFAULT_WHY]
    out["faq_items"] = [dict(i) for i in DEFAULT_FAQ]
    return out


def get():
    """What the front page says: the defaults, with whatever the owner changed on top. Cheap (cached); never raises."""
    try:
        cached = cache.get(CACHE_KEY)
        if cached is None:
            saved = HomeContent.load().content or {}
            cached = {**defaults(), **{k: v for k, v in saved.items() if k in defaults()}}
            cache.set(CACHE_KEY, cached, 300)
        return cached
    except Exception:  # noqa: BLE001 - a front page must not fail because its words could not be read
        return defaults()


def with_tokens(content, values):
    """The content with its tokens filled from live data (``values``: token name -> text)."""
    def fill(text):
        for name, value in values.items():
            text = text.replace("{" + name + "}", str(value))
        return text

    out = {}
    for key, value in content.items():
        if isinstance(value, str):
            out[key] = fill(value)
        elif key in ("why_items", "faq_items"):
            out[key] = [{k: fill(v) if isinstance(v, str) else v for k, v in item.items()} for item in value]
        else:
            out[key] = value
    return out


def _clean_text(value, label, kind):
    value = (value or "").replace("\r\n", "\n").strip()
    limit = 120 if kind in ("line", "title") else MAX_TEXT
    if len(value) > limit:
        raise ValidationError(f"\"{label}\" is longer than {limit} characters.")
    if kind == "line":
        value = " ".join(value.split())
    return value


def clean_rows(rows, kind):
    """The question or promise rows as typed: empty rows are dropped, a half-filled row is an error."""
    icons = {i for i, _ in ICONS}
    out = []
    for row in rows or ():
        if kind == "faq":
            first, second = (row.get("q") or "").strip(), (row.get("a") or "").strip()
            if not first and not second:
                continue
            if not first or not second:
                raise ValidationError("Each question needs both the question and its answer.")
            if len(first) > 200 or len(second) > 1200:
                raise ValidationError("A question can be 200 characters and an answer 1200.")
            out.append({"q": first, "a": second})
        else:
            title, text = (row.get("title") or "").strip(), (row.get("text") or "").strip()
            if not title and not text:
                continue
            if not title:
                raise ValidationError("Each promise needs a title.")
            if len(title) > 80 or len(text) > 240:
                raise ValidationError("A promise title can be 80 characters and its text 240.")
            icon = row.get("icon") if row.get("icon") in icons else "bi-shield-check"
            out.append({"icon": icon, "title": title, "text": text})
    if kind == "faq" and len(out) > MAX_FAQ:
        raise ValidationError(f"At most {MAX_FAQ} questions.")
    if kind != "faq" and len(out) > MAX_WHY:
        raise ValidationError(f"At most {MAX_WHY} promises.")
    return out


@transaction.atomic
def save(actor, values, faq_rows, why_rows, request=None):
    """Staff (``manage_settings``): save the front page's words. ``values`` holds the text fields and the section switches that were posted."""
    if not actor.has_perm(perm("manage_settings")):
        raise ServiceError("You do not have permission to perform this action.", code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)
    row = HomeContent.objects.select_for_update().get_or_create(pk=1)[0]
    base = defaults()
    content = dict(row.content or {})
    for section in SECTIONS:
        for key, label, kind, _default in section["fields"]:
            if key in values:
                text = _clean_text(values[key], label, kind)
                content[key] = text if text else base[key]  # an emptied box goes back to the standard words
    for toggle in TOGGLES:
        content[toggle] = bool(values.get(toggle))
    content["faq_items"] = clean_rows(faq_rows, "faq") or []
    content["why_items"] = clean_rows(why_rows, "why") or []
    changed = sorted(k for k in set(content) | set(row.content or {}) if content.get(k) != (row.content or {}).get(k))
    if changed:
        row.content = content
        row.version += 1
        row.save()
        cache.delete(CACHE_KEY)
        audit.record("branding.home_saved", actor=actor, target=row, metadata={"changed": changed[:40]}, request=request)
    return changed


def reset(actor, request=None):
    """Back to the standard words."""
    if not actor.has_perm(perm("manage_settings")):
        raise ServiceError("You do not have permission to perform this action.", code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)
    row = HomeContent.objects.get_or_create(pk=1)[0]
    row.content, row.version = {}, row.version + 1
    row.save()
    cache.delete(CACHE_KEY)
    audit.record("branding.home_reset", actor=actor, target=row, request=request)
