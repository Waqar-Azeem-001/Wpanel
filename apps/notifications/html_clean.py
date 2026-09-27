"""
A conservative HTML cleaner for email bodies a Super Admin writes or pastes.

Email HTML is a small, old subset of the web: tables, inline styles and a few text tags. This keeps exactly that and drops everything else
(scripts, styles blocks, frames, forms, event handlers, ``javascript:`` addresses, ``url()`` in styles...), so what is sent can never run
anything in a mail program and cannot pull a page into the message. It works on the *rendered* message (after the variables are filled in),
so a customer's name that contains markup cannot change the message either.

Anything not on a list is removed, never "fixed": tags not allowed disappear but their text stays; attributes not allowed disappear; a style
declaration that is not allowed disappears. The output is rebuilt from the parsed pieces, so tags are always balanced and quoted.
"""
import html
import re
from html.parser import HTMLParser

MAX_LENGTH = 100_000

ALLOWED_TAGS = {
    "a", "b", "strong", "i", "em", "u", "s", "small", "sub", "sup", "br", "hr", "p", "div", "span", "blockquote", "pre", "code",
    "h1", "h2", "h3", "h4", "ul", "ol", "li", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "img", "center",
}
VOID_TAGS = {"br", "hr", "img"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "frame", "frameset", "object", "embed", "applet", "form", "textarea", "select", "option",
                     "button", "svg", "math", "link", "meta", "head", "title", "noscript", "template", "base", "audio", "video", "canvas",
                     "input", "xmp", "plaintext", "noembed"}
GLOBAL_ATTRS = {"style", "title", "align", "dir", "lang"}
TAG_ATTRS = {
    "a": {"href", "target", "rel"}, "img": {"src", "alt", "width", "height"},
    "td": {"colspan", "rowspan", "width", "height", "valign", "bgcolor"}, "th": {"colspan", "rowspan", "width", "height", "valign", "bgcolor"},
    "table": {"width", "cellpadding", "cellspacing", "border", "bgcolor", "role"}, "tr": {"valign", "bgcolor"}, "ol": {"start"},
}
LINK_SCHEMES = ("http:", "https:", "mailto:", "tel:")
IMAGE_SCHEMES = ("http:", "https:", "cid:")
STYLE_PROPS = {
    "color", "background", "background-color", "font-size", "font-weight", "font-style", "font-family", "text-align", "text-decoration",
    "text-transform", "line-height", "letter-spacing", "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left", "border", "border-top", "border-right", "border-bottom",
    "border-left", "border-color", "border-style", "border-width", "border-radius", "border-collapse", "width", "max-width", "min-width",
    "height", "display", "vertical-align", "list-style-type", "white-space", "word-break",
}
DISPLAY_VALUES = {"block", "inline", "inline-block", "table", "table-cell", "table-row"}
BAD_STYLE_VALUE = re.compile(r"url\s*\(|expression|javascript|vbscript|behavio|@import|-moz-binding|\\|[<>{}]", re.I)
GOOD_STYLE_VALUE = re.compile(r"^[\w\s#%.,()+\-/\"'!]*$")
ATTR_NUMBER = re.compile(r"^\d{1,4}%?$")
ATTR_COLOR = re.compile(r"^#?[0-9a-zA-Z]{3,20}$")


def _clean_url(value, schemes):
    """The address if it is a plain web, mail, phone (or embedded image) address; else empty."""
    value = re.sub(r"[\x00-\x20\x7f]+", "", html.unescape(value or ""))  # the tricks that hide a scheme: spaces, tabs, newlines
    if not value or len(value) > 2000:
        return ""
    if value.startswith("#"):
        return value if schemes is LINK_SCHEMES and len(value) < 100 else ""
    return value if value.lower().startswith(schemes) else ""


def _clean_style(value):
    kept = []
    for declaration in (value or "").split(";"):
        name, sep, raw = declaration.partition(":")
        name, raw = name.strip().lower(), raw.strip()
        if not sep or name not in STYLE_PROPS or not raw or len(raw) > 200:
            continue
        if BAD_STYLE_VALUE.search(raw) or not GOOD_STYLE_VALUE.match(raw):
            continue
        if name == "display" and raw.lower() not in DISPLAY_VALUES:
            continue
        kept.append(f"{name}:{raw}")
    return ";".join(kept)


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.stack = []  # the allowed tags currently open
        self.skip = 0  # inside a tag whose content is dropped
        self.skip_tag = ""

    # -- attributes -------------------------------------------------------------------------------------------------------
    def _attrs(self, tag, attrs):
        allowed = GLOBAL_ATTRS | TAG_ATTRS.get(tag, set())
        kept = []
        for name, value in attrs:
            name = (name or "").lower()
            if name not in allowed:
                continue
            value = value or ""
            if name == "href":
                value = _clean_url(value, LINK_SCHEMES)
            elif name == "src":
                value = _clean_url(value, IMAGE_SCHEMES)
            elif name == "style":
                value = _clean_style(value)
            elif name in ("width", "height", "colspan", "rowspan", "cellpadding", "cellspacing", "border", "start"):
                value = value if ATTR_NUMBER.match(value.strip()) else ""
            elif name == "bgcolor":
                value = value if ATTR_COLOR.match(value.strip()) else ""
            elif name in ("align", "valign"):
                value = value.strip().lower() if value.strip().lower() in ("left", "right", "center", "top", "middle", "bottom") else ""
            elif name == "dir":
                value = value.strip().lower() if value.strip().lower() in ("ltr", "rtl") else ""
            elif name == "role":
                value = "presentation" if value.strip().lower() == "presentation" else ""
            elif name in ("target", "rel"):
                continue  # set below
            else:
                value = value[:200]
            if value != "" or name in ("alt",):
                kept.append((name, value))
        if tag == "a":
            if any(n == "href" for n, _ in kept):
                kept += [("target", "_blank"), ("rel", "noopener noreferrer")]
            else:
                kept = [(n, v) for n, v in kept if n != "href"]
        if tag == "img" and not any(n == "src" for n, _ in kept):
            return None
        return kept

    def _open(self, tag, attrs, closing_slash=False):
        if self.skip:
            if tag == self.skip_tag and tag not in VOID_TAGS:
                self.skip += 1
            return
        if tag in DROP_WITH_CONTENT:
            if tag not in VOID_TAGS and not closing_slash:
                self.skip, self.skip_tag = 1, tag
            return
        if tag not in ALLOWED_TAGS:
            return  # the tag goes, its text stays
        kept = self._attrs(tag, attrs)
        if kept is None:
            return
        text = "".join(f' {name}="{html.escape(value, quote=True)}"' for name, value in kept)
        if tag in VOID_TAGS:
            self.out.append(f"<{tag}{text}>")
        else:
            self.out.append(f"<{tag}{text}>")
            self.stack.append(tag)

    def handle_starttag(self, tag, attrs):
        self._open(tag.lower(), attrs)

    def handle_startendtag(self, tag, attrs):
        self._open(tag.lower(), attrs, closing_slash=True)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip:
            if tag == self.skip_tag:
                self.skip -= 1
            return
        if tag in VOID_TAGS or tag not in self.stack:
            return
        while self.stack:  # close whatever is open inside it first, so the result is always balanced
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))

    def handle_comment(self, data):
        return  # comments (and mail programs' conditional comments) are dropped

    def handle_decl(self, decl):
        return

    def handle_pi(self, data):
        return

    def unknown_decl(self, data):
        return


def clean(source):
    """The cleaned HTML fragment for ``source``. Empty in, empty out; too long is cut, never trusted."""
    source = (source or "")[:MAX_LENGTH]
    if not source.strip():
        return ""
    parser = _Cleaner()
    parser.feed(source)
    parser.close()
    while parser.stack:
        parser.out.append(f"</{parser.stack.pop()}>")
    return "".join(parser.out).strip()


def to_text(fragment):
    """A plain-text version of an HTML fragment (for the text part of an email): paragraphs, line breaks, list bullets, link addresses."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", fragment or "")
    text = re.sub(r'(?is)<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>', lambda m: f"{re.sub(r'<[^>]+>', '', m.group(2))} ({html.unescape(m.group(1))})"
                  if re.sub(r"<[^>]+>", "", m.group(2)).strip() not in ("", html.unescape(m.group(1))) else html.unescape(m.group(1)), text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h[1-4]|tr|blockquote|table)>", "\n\n", text)
    text = re.sub(r"(?i)</t[dh]>", "  ", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"(?i)</li>", "\n", text)
    text = re.sub(r"(?i)<hr[^>]*>", "\n----\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
