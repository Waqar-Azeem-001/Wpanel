"""The message HTML cleaner: the one gate between what a person (or a sender) writes and what the screen shows."""
import pytest

from apps.hosting.mailhtml import MAX_HTML, html_to_text, sanitize_html


def test_harmless_formatting_is_kept():
    html = "<p>Hello <b>bold</b>, <i>italic</i> and <u>underlined</u></p><ul><li>one</li><li>two</li></ul><blockquote>quote</blockquote>"
    assert sanitize_html(html) == html


@pytest.mark.parametrize("raw, gone", [
    ('<script>alert(1)</script>ok', "alert"),
    ('<style>p { color: red }</style>ok', "color"),
    ('<p onclick="x()">hi</p>', "onclick"),
    ('<img src="x" onerror="y()">hi', "img"),
    ('<iframe src="https://evil.example"></iframe>hi', "iframe"),
    ('<form action="/x"><input name="a"></form>hi', "form"),
    ('<svg onload="x()"><script>y()</script></svg>hi', "svg"),
    ('<a href="javascript:alert(1)">x</a>', "javascript"),
    ('<a href="  JaVaScRiPt:alert(1)">x</a>', "alert"),
    ('<a href="data:text/html;base64,AAAA">x</a>', "data:"),
    ('<p style="position:fixed">x</p>', "style"),
    ('<!-- <script>bad()</script> -->hi', "bad"),
])
def test_dangerous_things_are_removed(raw, gone):
    cleaned = sanitize_html(raw)
    assert gone.lower() not in cleaned.lower()
    assert "ok" in cleaned or "hi" in cleaned or "x" in cleaned or cleaned == ""


def test_links_keep_only_a_safe_address_and_open_safely():
    cleaned = sanitize_html('<a href="https://good.example/?a=1&b=2" onclick="x()" class="y">go</a><a href="mailto:me@x.com">m</a>')
    assert 'href="https://good.example/?a=1&amp;b=2"' in cleaned and 'href="mailto:me@x.com"' in cleaned
    assert cleaned.count('rel="noopener noreferrer nofollow"') == 2 and "onclick" not in cleaned and "class=" not in cleaned
    assert sanitize_html('<a href="/relative">x</a>') == "<span>x</span>"  # no safe address: just the text


def test_text_is_escaped_and_unclosed_tags_are_closed():
    assert sanitize_html("1 < 2 & 3 > 2") == "1 &lt; 2 &amp; 3 &gt; 2"
    assert sanitize_html("<div><i>open <u>tags") == "<div><i>open <u>tags</u></i></div>"
    assert sanitize_html("</b></div>stray") == "stray"  # closing tags that were never opened are ignored
    assert sanitize_html("<b>a</i>b</b>") == "<b>ab</b>"


def test_odd_input_never_raises_and_size_is_capped():
    assert sanitize_html("") == "" and sanitize_html(None) == ""
    assert sanitize_html("<<<>>> <b <i>") is not None
    assert len(sanitize_html("<p>" + "x" * (MAX_HTML * 3) + "</p>")) <= MAX_HTML
    assert "<" not in sanitize_html("&lt;script&gt;alert(1)&lt;/script&gt;").replace("&lt;", "")  # entities stay text


def test_html_to_text_keeps_line_breaks_and_drops_scripts():
    text = html_to_text("<p>Line one</p><p>Line two<br>three</p><ul><li>a</li><li>b</li></ul><script>bad()</script>")
    assert "Line one" in text and "three" in text and "- a" in text and "bad" not in text
    assert "&lt;" not in html_to_text("a &lt; b") and html_to_text("a &lt; b") == "a < b"
