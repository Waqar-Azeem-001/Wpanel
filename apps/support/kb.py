"""
The knowledgebase: public help articles grouped into categories (Getting Started, Hosting, Domains, cPanel, DNS,
Email, WordPress, Billing by default). Anyone may read *published* articles in *active* categories; only staff with
``manage_support`` change anything. Article bodies are plain text (paragraphs separated by blank lines) and are
rendered escaped, so an article can never inject markup.
"""
from django.db import transaction
from django.db.models import Count, Q
from rest_framework import status as http

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.core.exceptions import ServiceError
from apps.core.utils import unique_slug

from .models import KBArticle, KBCategory

MAX_BODY = 50000


def _require_manage(actor):
    if not (actor and actor.is_authenticated and actor.has_perm(perm("manage_support"))):
        raise ServiceError("You do not have permission to perform this action.", code="permission_denied",
                           status_code=http.HTTP_403_FORBIDDEN)


# --- Public reading --------------------------------------------------------------------------------

def public_categories():
    """Active categories with the number of published articles each holds (empty categories are hidden)."""
    return (KBCategory.objects.filter(is_active=True)
            .annotate(article_count=Count("articles", filter=Q(articles__is_published=True)))
            .filter(article_count__gt=0))


def public_articles(category=None):
    queryset = KBArticle.objects.filter(is_published=True, category__is_active=True).select_related("category")
    return queryset.filter(category=category) if category is not None else queryset


def search_public(term):
    term = (term or "").strip()
    if not term:
        return public_articles().none()
    return public_articles().filter(Q(title__icontains=term) | Q(body__icontains=term))


# --- Staff management ----------------------------------------------------------------------------------

@transaction.atomic
def save_category(actor, category, *, name, description="", sort_order=0, is_active=True, request=None):
    _require_manage(actor)
    created = category is None
    category = category or KBCategory()
    category.name, category.description = (name or "").strip(), (description or "").strip()
    category.sort_order, category.is_active = sort_order, is_active
    if created:
        category.slug = unique_slug(KBCategory.objects.all(), category.name, max_length=60)
    category.full_clean()
    category.save()
    audit.record("kb_category.created" if created else "kb_category.updated", actor=actor, target=category,
                 metadata={"name": category.name}, request=request)
    return category


@transaction.atomic
def save_article(actor, article, *, category, title, body, is_published=False, sort_order=0, request=None):
    _require_manage(actor)
    created = article is None
    article = article or KBArticle(created_by=actor)
    article.category, article.title = category, (title or "").strip()
    article.body, article.is_published, article.sort_order = (body or "").strip(), is_published, sort_order
    if len(article.body) > MAX_BODY:
        raise ServiceError(f"The article is too long (at most {MAX_BODY} characters).", code="article_too_long")
    if created or not article.slug:
        article.slug = unique_slug(KBArticle.objects.filter(category=category), article.title, max_length=80)
    article.full_clean()
    article.save()
    audit.record("kb_article.created" if created else "kb_article.updated", actor=actor, target=article,
                 metadata={"title": article.title, "published": is_published}, request=request)
    return article


@transaction.atomic
def delete_article(actor, article, *, request=None):
    _require_manage(actor)
    audit.record("kb_article.deleted", actor=actor, metadata={"title": article.title, "id": article.pk},
                 request=request)
    article.delete()
