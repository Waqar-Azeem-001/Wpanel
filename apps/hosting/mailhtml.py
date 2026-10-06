"""
Safe HTML for the mail composer and viewer. A message written in the rich editor travels as HTML, and a received message may
contain anything, so every piece of HTML is reduced to a small allowlist of harmless tags before it is stored or shown.

The rules: only the tags in ``ALLOWED`` survive (their attributes are dropped, except a link's address, which must be http,
https or mailto and always opens safely); script, style and similar elements are removed WITH their content; comments,
event handlers, inline styles, images, forms, iframes and everything unknown vanish (their text stays, escaped); text is
always escaped; unclosed tags are closed; the result is cut to ``MAX_HTML`` characters.
"""
import html
from html.parser import HTMLParser
from urllib.parse import urlsplit

MAX_HTML = 60_000
ALLOWED = {"b", "strong", "i", "em", "u", "s", "strike", "br", "p", "div", "span", "ul", "ol", "li", "blockquote", "a",
           "h1", "h2", "h3", "pre", "code", "hr"}
VOID = {"br", "hr"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript", "head", "title", "svg", "math",
                     "textarea", "select", "option", "button"}
SAFE_LINK_SCHEMES = {"http", "https", "mailto"}
BLOCK_TAGS = {"p", "div", "li", "blockquote", "h1", "h2", "h3", "pre", "ul", "ol", "hr"}


def _safe_href(value):
    value = (value or "").strip()
    if not value or any(ord(ch) < 32 for ch in value):
        return ""
    return value if urlsplit(value).scheme.lower() in SAFE_LINK_SCHEMES else ""


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.open, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED:
            return
        if tag == "a":
            href = _safe_href(dict(attrs).get("href"))
            if href:
                self.out.append(f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer nofollow">')
                self.open.append(("a", "a"))
            else:  # a link without a safe address is just its text
                self.out.append("<span>")
                self.open.append(("a", "span"))
            return
        self.out.append(f"<{tag}>")
        if tag not in VOID:
            self.open.append((tag, tag))

    def handle_startendtag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT or self.skip:
            return  # a self-closed script or style has no content to wait for
        self.handle_starttag(tag, attrs)
        if tag in ALLOWED and tag not in VOID and self.open and self.open[-1][0] == tag:
            self.out.append(f"</{self.open.pop()[1]}>")

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in ALLOWED or tag in VOID:
            return
        if any(source == tag for source, _ in self.open):
            while self.open:
                source, emitted = self.open.pop()
                self.out.append(f"</{emitted}>")
                if source == tag:
                    break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data))

    def close(self):
        super().close()
        while self.open:
            self.out.append(f"</{self.open.pop()[1]}>")


def sanitize_html(raw) -> str:
    """The message HTML reduced to the allowlist (see the module notes). Never raises on odd input."""
    if not raw:
        return ""
    cleaner = _Cleaner()
    try:
        cleaner.feed(str(raw)[:MAX_HTML * 2])
        cleaner.close()
    except Exception:  # noqa: BLE001 - broken markup must never break the screen: fall back to escaped text
        return html.escape(str(raw))[:MAX_HTML]
    return "".join(cleaner.out)[:MAX_HTML]


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.skip += 1
        elif tag == "br" or tag in BLOCK_TAGS:
            self.parts.append(chr(10))
            if tag == "li":
                self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append(chr(10))

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(raw) -> str:
    """Readable plain text from message HTML (line breaks kept), for the text copy of a message and for snippets."""
    parser = _Text()
    try:
        parser.feed(str(raw or "")[:MAX_HTML * 2])
        parser.close()
    except Exception:  # noqa: BLE001
        return ""
    lines = [line.rstrip() for line in "".join(parser.parts).splitlines()]
    text = chr(10).join(lines).strip()
    while chr(10) * 3 in text:
        text = text.replace(chr(10) * 3, chr(10) * 2)
    return text
