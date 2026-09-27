"""
Email wording that a Super Admin can change without a code change.

Every email the portal sends has a default written in ``templates/emails/`` (a subject and a plain-text body; the branded HTML layout
is built from the text). A Super Admin may replace the subject and the text of any of them here. The replacement is stored in the
database (``EmailTemplateOverride``); "reset" deletes it and the default is used again.

Replacements are rendered in a locked-down template engine: only the built-in tags and filters (``if``, ``for``, ``date``,
``default``...), no files can be included, and the person is exposed as a small safe object (name and email only, never the password
hash). What can appear in a message is the list of variables shown beside the editor for that message.
"""
import re

from django.template import Context, Engine, TemplateSyntaxError
from django.template.loader import get_template

from apps.core.exceptions import ServiceError

from . import events, html_clean
from .models import EmailTemplateOverride

FILTERS = ["apps.notifications.mail_filters"]  # date and time that also cope with a preview's stand-ins
ENGINE = Engine(dirs=[], app_dirs=False, autoescape=False, debug=False, libraries={}, builtins=FILTERS)
HTML_ENGINE = Engine(dirs=[], app_dirs=False, autoescape=True, debug=False, libraries={}, builtins=FILTERS)  # a design: variables are escaped
MAX_HTML = 60_000
VAR_RE = re.compile(r"{{\s*([A-Za-z_][\w.]*)")
ALWAYS = ("site_name", "site_url", "user.get_short_name", "user.first_name", "user.last_name", "user.email")


def known_templates():
    """{template key: [event labels that use it]} for every email the portal can send."""
    found = {}
    for event in events.EVENTS.values():
        if event.email_template:
            found.setdefault(event.email_template, []).append(event.label)
    return found


def _source(name):
    return get_template(name).template.source


def default_source(key):
    """(subject, body) as written in the files."""
    return _source(f"emails/{key}_subject.txt").strip(), _source(f"emails/{key}.txt")


def variables(key):
    """The names a message may use: what the default text uses, plus the site name and address."""
    subject, body = default_source(key)
    names = set(VAR_RE.findall(subject + body)) | set(ALWAYS)
    return sorted(names)


def customised(key):
    return EmailTemplateOverride.objects.filter(template=key).first()


class SafeUser:
    """What an email may show about the person it is for: never a password hash, permissions or anything else."""

    def __init__(self, user):
        self._user = user

    def __getattr__(self, name):
        if name in ("first_name", "last_name", "email", "full_name", "get_short_name", "get_full_name"):
            return getattr(self._user, name)
        raise AttributeError(name)

    def __bool__(self):
        return self._user is not None


def _compile(source):
    try:
        return ENGINE.from_string(source)
    except TemplateSyntaxError as exc:
        raise ServiceError(f"The wording has a mistake: {exc}", code="template_invalid")


def render(subject_source, body_source, context):
    ctx = dict(context)
    if ctx.get("user") is not None:
        ctx["user"] = SafeUser(ctx["user"])
    try:
        # A Context escapes what fills a variable unless told not to, whatever its engine says: plain text must not (O'Brien, A & B).
        subject = " ".join(_compile(subject_source).render(Context(ctx, autoescape=False)).split())
        return subject, _compile(body_source).render(Context(ctx, autoescape=False))
    except ServiceError:
        raise
    except Exception as exc:  # noqa: BLE001 - a file that cannot be included, a bad filter argument: all "the wording does not work"
        raise ServiceError(f"The wording could not be used: {type(exc).__name__}: {exc}", code="template_invalid")


TAG_RE = re.compile(r"({{.*?}}|{%.*?%})", re.S)


def _escape_outside_tags(value):
    """HTML-escape text but leave the template's own {{ }} and {% %} as written (a condition may hold a > or a quote)."""
    import html as html_module

    return "".join(part if TAG_RE.fullmatch(part) else html_module.escape(part, quote=False) for part in TAG_RE.split(value))


def text_to_design(text):
    """A plain-text message as a simple HTML design: what the editor starts from when a message has no design of its own yet."""
    blocks = []
    text = re.sub(r"{%\s*(end)?autoescape[^%]*%}", "", text or "")  # the text files switch escaping off; a design escapes what fills a variable
    for block in re.split(r"\n\s*\n", text.strip()):
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if all(line.lstrip().startswith(("- ", "* ")) for line in lines):
            items = "".join(f"<li>{_escape_outside_tags(line.lstrip()[2:].strip())}</li>" for line in lines)
            blocks.append(f"<ul>{items}</ul>")
        else:
            body = "<br>".join(_escape_outside_tags(line.strip()) for line in lines)
            blocks.append(f"<p>{body}</p>")
    return "\n".join(blocks)


def _compile_html(source):
    try:
        return HTML_ENGINE.from_string(source)
    except TemplateSyntaxError as exc:
        raise ServiceError(f"The design has a mistake: {exc}", code="template_invalid")


def render_html(source, context):
    """A design filled in and cleaned: variables are escaped as they are written, then anything an email must not carry is removed."""
    ctx = dict(context)
    if ctx.get("user") is not None:
        ctx["user"] = SafeUser(ctx["user"])
    try:
        return html_clean.clean(_compile_html(source).render(Context(ctx, autoescape=True)))
    except ServiceError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(f"The design could not be used: {type(exc).__name__}: {exc}", code="template_invalid")


def render_override(override, context):
    """(subject, text, html) for a customised message. ``html`` is empty for a text-only message; the text is made from the design if blank."""
    subject, text = render(override.subject, override.body_text, context)
    if not (override.body_html or "").strip():
        return subject, text, ""
    fragment = render_html(override.body_html, context)
    return subject, (text if text.strip() else html_clean.to_text(fragment)), fragment


class _Placeholder:
    """Stands in for any variable in a preview: shows its name, and survives being called or drilled into."""

    def __init__(self, name):
        self._name = name

    def __str__(self):
        return f"«{self._name}»"

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Placeholder(f"{self._name}.{name}")

    def __call__(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter([_Placeholder(f"{self._name} item")])

    def __bool__(self):
        return True


def preview(key, subject, body, html=""):
    """The wording rendered with every variable shown as «its name», so the shape can be checked before saving.
    Returns (subject, text, design) where the design is the cleaned HTML fragment, or empty."""
    context = {name.split(".")[0]: _Placeholder(name.split(".")[0]) for name in variables(key)}
    subject_out, text = render(subject, body, context) if (body or "").strip() else (render(subject, "x", context)[0], "")
    fragment = render_html(html, context) if (html or "").strip() else ""
    if fragment and not text.strip():
        text = html_clean.to_text(fragment)
    return subject_out, text, fragment


def validate(key, subject, body, html=""):
    """Refuse wording that cannot work: a mistake in the template, an empty message, or a secret link missing.
    Returns (subject, text, html) as they will be stored."""
    if key not in known_templates():
        raise ServiceError("Unknown message.", code="unknown_template")
    subject, body, html = (subject or "").strip(), (body or "").strip(), (html or "").strip()
    if not subject or not (body or html):
        raise ServiceError("Give the message a subject and some text or a design.", code="template_empty")
    if len(subject) > 255 or "\n" in subject:
        raise ServiceError("The subject is one line of at most 255 characters.", code="template_invalid")
    if len(html) > MAX_HTML:
        raise ServiceError(f"The design is too long (at most {MAX_HTML:,} characters).", code="template_invalid")
    _, _, fragment = preview(key, subject, body, html)  # compiles and renders both: raises on a mistake
    if html and not fragment.strip():
        raise ServiceError("Nothing of that design can be sent: it holds no text or picture an email may carry.", code="template_empty")
    if key in events.sensitive_templates() and "link" in variables(key):
        written = body + html
        if "{{ link" not in written and "{{link" not in written:
            raise ServiceError("This message carries a one-time link. Keep {{ link }} in the message, or the person cannot use it.",
                               code="template_link_missing")
    return subject, body, html
