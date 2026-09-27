"""
Designed emails (E10): a Super Admin can write a message in a visual editor or paste HTML; what is sent is cleaned; the logo travels inside
the email; the plain-text version is made from the design when it is left blank.
"""
from io import BytesIO
from types import SimpleNamespace

import pytest
from django.core import mail
from django.test import Client as HttpClient
from django.urls import reverse
from PIL import Image

from apps.accounts.roles import Role
from apps.branding import services as branding
from apps.core.exceptions import ServiceError
from apps.notifications import html_clean
from apps.notifications import services as notifications
from apps.notifications.models import EmailMessage, EmailTemplateOverride

pytestmark = pytest.mark.django_db


def browser(user=None):
    client = HttpClient(HTTP_HOST="localhost", raise_request_exception=False)
    if user is not None:
        client.force_login(user)
    return client


@pytest.fixture
def boss(staff):
    return staff(Role.SUPER_ADMIN)


def png(color=(30, 90, 200)):
    buffer = BytesIO()
    Image.new("RGB", (60, 20), color).save(buffer, "PNG")
    return buffer.getvalue()


# --- The cleaner ------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("dirty,forbidden", [
    ("<script>alert(1)</script><p>hi</p>", "script"), ('<p onclick="x()">hi</p>', "onclick"), ('<img src="x" onerror="alert(1)">', "onerror"),
    ('<a href="javascript:alert(1)">x</a>', "javascript"), ('<a href="  jAvA\tscript:alert(1)">x</a>', "script:"),
    ('<a href="&#106;avascript:alert(1)">x</a>', "javascript"), ('<a href="data:text/html,<b>x</b>">x</a>', "data:"),
    ('<iframe src="https://evil.test"></iframe>', "iframe"), ('<form action="/x"><input name="a"></form>', "<form"),
    ("<style>p{color:red}</style><p>x</p>", "<style"), ('<object data="x"></object>', "object"), ('<svg onload="x()"></svg>', "svg"),
    ('<p style="background:url(https://evil.test/x.png)">x</p>', "url("), ('<p style="width:expression(alert(1))">x</p>', "expression"),
    ("<!--[if mso]><b>x</b><![endif]-->", "[if"), ('<link rel="stylesheet" href="https://evil.test/x.css">', "<link"),
    ('<meta http-equiv="refresh" content="0;url=https://evil.test">', "<meta"), ('<base href="https://evil.test/">', "<base"),
    ('<p style="position:fixed;top:0">x</p>', "position"), ('<button onclick="x()">go</button>', "button"),
])
def test_anything_a_mail_program_should_not_run_is_removed(dirty, forbidden):
    assert forbidden.lower() not in html_clean.clean(dirty).lower()


def test_the_text_of_a_removed_tag_stays_and_the_rest_is_kept_and_balanced():
    assert html_clean.clean("<blink>hello</blink> <marquee>there</marquee>") == "hello there"
    cleaned = html_clean.clean('<p style="color:#123456;font-size:14px">a <b>b <i>c</p> d')
    assert cleaned == '<p style="color:#123456;font-size:14px">a <b>b <i>c</i></b></p> d'  # closed in the right order
    table = '<table width="600" cellpadding="0" style="border-collapse:collapse"><tr><td align="center" bgcolor="#ffffff" colspan="2">x</td></tr></table>'
    assert html_clean.clean(table) == table


def test_links_open_safely_and_only_web_mail_and_phone_addresses_pass():
    cleaned = html_clean.clean('<a href="https://shop.example/pay" target="_self" rel="x">Pay</a> <a href="mailto:a@b.test">m</a> <a href="tel:+92300">t</a> '
                               '<a href="/relative">r</a> <a href="//evil.test/x">p</a> <a>nothing</a>')
    assert 'href="https://shop.example/pay" target="_blank" rel="noopener noreferrer"' in cleaned
    assert 'href="mailto:a@b.test"' in cleaned and 'href="tel:+92300"' in cleaned
    assert 'href="/relative"' not in cleaned and 'href="//evil.test/x"' not in cleaned and "<a>nothing</a>" in cleaned


def test_pictures_may_be_web_or_embedded_but_never_a_script_carrier():
    assert '<img src="cid:brand-logo" alt="Logo">' in html_clean.clean('<img src="cid:brand-logo" alt="Logo">')
    assert html_clean.clean('<img src="https://cdn.example/a.png" width="120">') == '<img src="https://cdn.example/a.png" width="120">'
    assert html_clean.clean('<img src="javascript:alert(1)">') == "" and html_clean.clean("<img>") == ""


def test_the_content_of_a_script_or_a_style_block_goes_with_it_and_a_stray_closing_tag_closes_nothing():
    assert html_clean.clean("<style>p{color:red}</style><p>x</p>") == "<p>x</p>"
    assert html_clean.clean("<p>a<script>alert(1)</script>b</p>") == "<p>ab</p>"
    assert html_clean.clean("<p>a</b>b</p><p>c</p>") == "<p>ab</p><p>c</p>"  # </b> was never opened: it must not close the paragraph
    assert html_clean.clean('<a href=" https://ok.test/pay ">x</a>').startswith('<a href="https://ok.test/pay"')  # stray spaces are trimmed


def test_markup_in_text_is_shown_as_text_never_run():
    assert html_clean.clean("<p>a &lt;script&gt;alert(1)&lt;/script&gt; b</p>") == "<p>a &lt;script&gt;alert(1)&lt;/script&gt; b</p>"


def test_an_empty_design_stays_empty_and_a_huge_one_is_cut():
    assert html_clean.clean("") == "" and html_clean.clean("   ") == "" and html_clean.clean(None) == ""
    assert len(html_clean.clean("<p>x</p>" * 50_000)) <= html_clean.MAX_LENGTH * 3


def test_a_plain_text_version_is_made_from_a_design():
    text = html_clean.to_text('<h2>Hello</h2><p>Line one<br>Line two</p><ul><li>One</li><li>Two</li></ul><p><a href="https://x.test/pay">Pay now</a></p>')
    assert "Hello" in text and "Line one\nLine two" in text and "- One" in text and "Pay now (https://x.test/pay)" in text and "<" not in text


# --- Rendering a design -----------------------------------------------------------------------------------------------------------------

DESIGN = ('<h2>Hello {{ user.get_short_name }}</h2><p>Your invoice <strong>{{ invoice.number }}</strong> is ready.</p>'
          '<p><a href="{{ link }}" style="display:inline-block;padding:12px 24px;background:#2459d6;color:#ffffff">Pay now</a></p><script>alert(1)</script>')


def test_a_design_is_filled_in_escaped_cleaned_and_put_inside_the_branded_layout(boss, customer):
    notifications.save_template(boss, "invoice_issued", "Invoice {{ invoice.number }}", "", DESIGN)
    override = EmailTemplateOverride.objects.get(template="invoice_issued")
    assert override.body_html == DESIGN and override.body_text == ""
    invoice = SimpleNamespace(number="INV-1<b>x</b>")
    _subject, text, html = notifications.render_email("invoice_issued", {"user": customer, "invoice": invoice, "link": "/account/billing/1/", "items": []})
    assert "<strong>INV-1&lt;b&gt;x&lt;/b&gt;</strong>" in html  # what filled the variable was escaped, so it stayed text
    assert "<script>" not in html and "alert(1)" not in html  # the pasted script was removed
    assert "<html" in html and "Pay now" in html  # inside the branded shell
    assert "Your invoice INV-1" in text and "Pay now" in text  # the text version is made from the design
    assert html.count("Pay now") == 1 and "View and pay the invoice" not in html  # the layout adds no button beside the design's own
    assert "<h1" not in html  # the design brings its own headline: the layout adds none


def test_a_written_plain_text_version_is_used_as_it_is(boss, customer):
    notifications.save_template(boss, "password_changed_by_admin", "Changed", "Your password was changed, {{ user.get_short_name }}.", "<p>Changed.</p>")
    _subject, text, html = notifications.render_email("password_changed_by_admin", {"user": customer})
    assert text.strip() == f"Your password was changed, {customer.get_short_name()}." and "Changed." in html


def test_without_a_design_a_message_is_text_in_the_standard_layout_as_before(boss, customer):
    notifications.save_template(boss, "password_changed_by_admin", "Changed", "Plain words for {{ user.get_short_name }}.")
    _subject, text, html = notifications.render_email("password_changed_by_admin", {"user": customer})
    assert html == "" and "Plain words" in text and not EmailTemplateOverride.objects.get(template="password_changed_by_admin").body_html


def test_a_broken_design_is_refused_before_it_is_stored_and_a_broken_stored_one_falls_back(boss, customer):
    with pytest.raises(ServiceError):
        notifications.save_template(boss, "invoice_issued", "S", "", "<p>{% if %}</p>")
    with pytest.raises(ServiceError):
        notifications.save_template(boss, "invoice_issued", "S", "", "<script>x()</script>")  # nothing a mail may carry
    with pytest.raises(ServiceError):
        notifications.save_template(boss, "invoice_issued", "S", "", "<p>x</p>" + "y" * 70_000)
    assert not EmailTemplateOverride.objects.exists()
    EmailTemplateOverride.objects.create(template="password_changed_by_admin", subject="S", body_text="", body_html="<p>{% nope %}</p>")
    subject, text, _html = notifications.render_email("password_changed_by_admin", {"user": customer})
    assert subject and text  # the default was sent


def test_a_message_with_a_one_time_link_must_keep_the_link_in_its_design(boss):
    with pytest.raises(ServiceError) as caught:
        notifications.save_template(boss, "password_reset", "Reset", "", "<p>Hello, click nothing.</p>")
    assert caught.value.code == "template_link_missing"
    notifications.save_template(boss, "password_reset", "Reset", "", '<p><a href="{{ link }}">Reset it</a></p>')


def test_the_design_never_reaches_the_password_hash(boss, customer):
    notifications.save_template(boss, "password_changed_by_admin", "S", "", "<p>{{ user.password }}</p><p>{{ user.email }}</p>")
    _subject, _text, html = notifications.render_email("password_changed_by_admin", {"user": customer})
    assert customer.password not in html and customer.email in html


def test_plain_text_wording_keeps_apostrophes_and_ampersands_as_they_are_and_a_design_escapes_them(boss):
    from apps.notifications import mail_templates

    who = {"name": "O'Brien & Sons <Ltd>"}
    subject, body = mail_templates.render("Hi {{ name }}", "Dear {{ name }}", who)
    assert (subject, body) == ("Hi O'Brien & Sons <Ltd>", "Dear O'Brien & Sons <Ltd>")  # text is text: no &amp; or &#x27;
    assert mail_templates.render_html("<p>Dear {{ name }}</p>", who) == "<p>Dear O'Brien &amp; Sons &lt;Ltd&gt;</p>"


def test_every_default_message_can_be_previewed_as_text_and_as_a_design_even_with_date_filters():
    from apps.notifications import mail_templates

    for key in mail_templates.known_templates():
        subject, body = mail_templates.default_source(key)
        shown_subject, text, fragment = mail_templates.preview(key, subject, body, mail_templates.text_to_design(body))
        assert shown_subject and text.strip() and fragment.strip(), key


def test_the_date_filter_still_formats_a_real_date():
    import datetime

    from apps.notifications import mail_templates

    assert mail_templates.render("s", "{{ when|date:'d M Y' }}", {"when": datetime.date(2026, 9, 27)})[1] == "27 Sep 2026"


# --- The editor page --------------------------------------------------------------------------------------------------------------------

def test_the_editor_page_offers_plain_text_and_a_designed_message_and_starts_from_the_default(boss):
    page = browser(boss).get(reverse("notifications_staff:template", args=["invoice_issued"])).content.decode()
    for needle in ('name="mode" value="text" checked', 'name="mode" value="design"', 'data-cmd="bold"', 'data-cmd="button"', 'data-me-tab="html"',
                   'name="body_html"', "js/mail_editor.js", 'data-insert="invoice.number"'):
        assert needle in page, needle
    assert "autoescape" not in page.split('name="body_html"')[1].split("</textarea>")[0]
    assert "<p>" in page.split('name="body_html"')[1].split("</textarea>")[0].replace("&lt;", "<").replace("&gt;", ">")  # the default text, as paragraphs, is the start


def test_saving_a_design_from_the_page_and_saving_plain_text_clears_it(boss):
    url = reverse("notifications_staff:template", args=["invoice_issued"])
    saved = browser(boss).post(url, {"do": "save", "subject": "Invoice {{ invoice.number }}", "mode": "design", "body_html": "<p>Hi {{ invoice.number }}</p>", "body_text": ""})
    assert saved.status_code == 302 and EmailTemplateOverride.objects.get(template="invoice_issued").body_html == "<p>Hi {{ invoice.number }}</p>"
    page = browser(boss).get(url).content.decode()
    assert 'name="mode" value="design" checked' in page
    browser(boss).post(url, {"do": "save", "subject": "Invoice {{ invoice.number }}", "mode": "text", "body_html": "<p>ignored</p>", "body_text": "Words {{ invoice.number }}"})
    row = EmailTemplateOverride.objects.get(template="invoice_issued")
    assert row.body_html == "" and row.body_text == "Words {{ invoice.number }}"


def test_the_preview_shows_the_design_in_a_sandboxed_frame_and_stores_nothing(boss):
    url = reverse("notifications_staff:template", args=["invoice_issued"])
    page = browser(boss).post(url, {"do": "preview", "subject": "S {{ invoice.number }}", "mode": "design",
                                    "body_html": "<h2>Hi</h2><p>{{ invoice.number }}</p><script>alert(1)</script>", "body_text": ""}).content.decode()
    frame = page.split('srcdoc="')[1].split('" title=')[0]
    assert frame.strip().startswith("&lt;!doctype html&gt;") and "&lt;/html&gt;" in frame and '"' not in frame  # the whole page, escaped into the attribute
    assert 'class="me-preview" sandbox' in page and "«invoice.number»" in frame and "alert(1)" not in frame
    assert not EmailTemplateOverride.objects.exists()


def test_only_a_super_admin_can_save_a_design(staff):
    url = reverse("notifications_staff:template", args=["invoice_issued"])
    browser(staff(Role.ADMIN)).post(url, {"do": "save", "subject": "S", "mode": "design", "body_html": "<p>x</p>"})
    assert not EmailTemplateOverride.objects.exists()


# --- The logo travels inside the email --------------------------------------------------------------------------------------------------

def test_the_logo_is_carried_inside_the_email_so_it_shows_without_reaching_the_portal(boss, customer):
    branding.save_settings(boss, logo=png())
    notifications.send_email(to_email=customer.email, template="order_placed", context={"user": customer}, user=customer)
    message = mail.outbox[-1]
    html = next(body for body, kind in message.alternatives if kind == "text/html")
    assert 'src="cid:brand-logo"' in html and "/brand/logo/" not in html
    inline = [part for part in message.attachments if getattr(part, "get", None) and part.get("Content-ID") == "<brand-logo>"]
    assert len(inline) == 1 and inline[0].get_content_type() == "image/png" and message.mixed_subtype == "related"
    assert "/brand/logo/" in EmailMessage.objects.latest("pk").body_html  # the stored copy (what the log shows) keeps the address


def test_without_a_logo_nothing_is_embedded_and_a_broken_image_never_stops_the_email(boss, customer, monkeypatch):
    notifications.send_email(to_email=customer.email, template="order_placed", context={"user": customer}, user=customer)
    assert "cid:" not in next(body for body, kind in mail.outbox[-1].alternatives if kind == "text/html")
    branding.save_settings(boss, logo=png())

    def boom(kind):
        raise RuntimeError("disk")

    monkeypatch.setattr(branding, "image", boom)
    notifications.send_email(to_email=customer.email, template="order_placed", context={"user": customer}, user=customer)
    assert mail.outbox[-1].to == [customer.email]
