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

from . import events
from .models import EmailTemplateOverride

ENGINE = Engine(dirs=[], app_dirs=False, autoescape=False, debug=False, libraries={})
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
        subject = " ".join(_compile(subject_source).render(Context(ctx)).split())
        return subject, _compile(body_source).render(Context(ctx))
    except ServiceError:
        raise
    except Exception as exc:  # noqa: BLE001 - a file that cannot be included, a bad filter argument: all "the wording does not work"
        raise ServiceError(f"The wording could not be used: {type(exc).__name__}: {exc}", code="template_invalid")


def render_override(override, context):
    """(subject, body text) for a customised message."""
    return render(override.subject, override.body_text, context)


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


def preview(key, subject, body):
    """The wording rendered with every variable shown as «its name», so the shape can be checked before saving."""
    context = {name.split(".")[0]: _Placeholder(name.split(".")[0]) for name in variables(key)}
    return render(subject, body, context)


def validate(key, subject, body):
    """Refuse wording that cannot work: a mistake in the template, an empty message, or a secret link missing."""
    if key not in known_templates():
        raise ServiceError("Unknown message.", code="unknown_template")
    subject, body = (subject or "").strip(), (body or "").strip()
    if not subject or not body:
        raise ServiceError("Give the message a subject and some text.", code="template_empty")
    if len(subject) > 255 or "\n" in subject:
        raise ServiceError("The subject is one line of at most 255 characters.", code="template_invalid")
    preview(key, subject, body)  # compiles and renders: raises on a mistake
    if key in events.sensitive_templates() and "link" in variables(key) and "{{ link" not in body and "{{link" not in body:
        raise ServiceError("This message carries a one-time link. Keep {{ link }} in the text, or the person cannot use it.",
                           code="template_link_missing")
    return subject, body
