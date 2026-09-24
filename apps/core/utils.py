from django.utils.text import slugify


def client_ip(request):
    """Client IP. X-Forwarded-For is trusted because Nginx (our proxy) overwrites it."""
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def unique_slug(queryset, text, *, field="slug", exclude_pk=None, max_length=50):
    """
    A URL-safe slug from ``text``, unique within ``queryset``, appending ``-2``,
    ``-3``... on collision. ``queryset`` should be the model's base manager
    (unfiltered), and ``exclude_pk`` lets an update keep its own slug.
    """
    base = slugify(text)[:max_length] or "item"
    candidate = base
    suffix = 2
    while queryset.filter(**{field: candidate}).exclude(pk=exclude_pk).exists():
        tail = f"-{suffix}"
        candidate = f"{base[:max_length - len(tail)]}{tail}"
        suffix += 1
    return candidate
