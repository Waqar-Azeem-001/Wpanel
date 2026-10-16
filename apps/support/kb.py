"""
The knowledgebase: public help articles grouped into categories (Getting Started, Hosting, Domains, cPanel, DNS,
Email, WordPress, Billing by default). Anyone may read *published* articles in *active* categories; only staff with
``manage_support`` change anything. Article bodies are plain text (paragraphs separated by blank lines) and are
rendered escaped, so an article can never inject markup.
"""
import re

from django.db import transaction
from django.db.models import Count, F, Q
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.text import slugify
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


SCHEMA = "https" + "://schema.org"
LINK = re.compile(r"\[([^\]\n]{1,120})\]\((/[^\s)]{0,200}|" + "https?:" + r"//[^\s)]{4,200})\)")
BOLD = re.compile(r"\*\*([^*\n]{1,200})\*\*")
CODE = re.compile(r"`([^`\n]{1,200})`")


def _inline(text):
    """One line of an article as safe HTML: everything escaped first, then bold, code and links from a small allowed set."""
    html = escape(text)
    html = CODE.sub(r"<code>\1</code>", html)
    html = BOLD.sub(r"<strong>\1</strong>", html)
    return LINK.sub(lambda m: f'<a href="{m.group(2)}"' + (' rel="noopener noreferrer" target="_blank"' if not m.group(2).startswith("/") else "") + f">{m.group(1)}</a>", html)


WIKI = re.compile(r"\[\[([^\]\n]{3,140})\]\]")


def link_articles(text):
    """[[Exact article title]] becomes a link to that article when it is published, and plain words when it is not."""
    cache = {}

    def swap(match):
        title = match.group(1).strip()
        if title not in cache:
            found = public_articles().filter(title__iexact=title).first()
            cache[title] = f"[{found.title}](/help/{found.category.slug}/{found.slug}/)" if found else title
        return cache[title]

    return WIKI.sub(swap, text or "")


def render_body(text):
    """(html, contents). Paragraphs, ## and ### headings, bullet and numbered lists, > notes, **bold**, `code` and links. Nothing else gets through: the text is escaped first.
    Numbered lists become steps, bullets get ticks, and the "Frequently asked questions" section becomes questions that open to their answers."""
    text = link_articles(text)
    out, toc, used = [], [], set()
    in_faq = open_question = False

    def close_question():
        nonlocal open_question
        if open_question:
            out.append("</div></details>")
            open_question = False

    blocks = [b for b in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()) if b.strip()]
    for block in blocks:
        lines = [l.rstrip() for l in block.split("\n") if l.strip()]
        first = lines[0].lstrip()
        if first.startswith("## ") or first.startswith("### "):
            level = 2 if first.startswith("## ") else 3
            heading = first[level + 1:].strip()
            lines = lines[1:]
            if level == 2:
                close_question()
                if in_faq:
                    out.append("</section>")
                    in_faq = False
                anchor = slugify(heading) or "section"
                while anchor in used:
                    anchor += "-x"
                used.add(anchor)
                toc.append((anchor, heading))
                if heading.lower().startswith("frequently asked"):
                    in_faq = True
                    out.append(f'<section class="help-faqs"><h2 id="{anchor}">{_inline(heading)}</h2>')
                else:
                    out.append(f'<h2 id="{anchor}">{_inline(heading)}</h2>')
            elif in_faq:
                close_question()
                out.append(f'<details class="help-faq"><summary>{_inline(heading)}</summary><div>')
                open_question = True
            else:
                anchor = slugify(heading) or "section"
                while anchor in used:
                    anchor += "-x"
                used.add(anchor)
                out.append(f'<h3 id="{anchor}">{_inline(heading)}</h3>')
            if not lines:
                continue
            first = lines[0].lstrip()
        if all(re.match(r"^\s*[-*] ", l) for l in lines):
            out.append('<ul class="help-checks">' + "".join(f"<li>{_inline(re.sub(r'^\s*[-*] ', '', l))}</li>" for l in lines) + "</ul>")
        elif all(re.match(r"^\s*\d+[.)] ", l) for l in lines):
            out.append('<ol class="help-steps">' + "".join(f"<li>{_inline(re.sub(r'^\s*\d+[.)] ', '', l))}</li>" for l in lines) + "</ol>")
        elif first.startswith(">"):
            out.append('<aside class="help-note">' + "<br>".join(_inline(l.lstrip("> ").strip()) for l in lines) + "</aside>")
        else:
            out.append("<p>" + "<br>".join(_inline(l.strip()) for l in lines) + "</p>")
    close_question()
    if in_faq:
        out.append("</section>")
    return mark_safe("".join(out)), toc


def reading_minutes(text):
    return max(1, round(len((text or "").split()) / 200))


def featured(limit=6):
    return public_articles().filter(is_featured=True).order_by("sort_order", "title")[:limit]


def popular(limit=6):
    return public_articles().filter(views__gt=0).order_by("-views", "title")[:limit]


def related(article, limit=4):
    return public_articles().filter(category=article.category).exclude(pk=article.pk).order_by("sort_order", "title")[:limit]


def neighbours(article):
    """The articles before and after this one in its category."""
    rows = list(public_articles(article.category).order_by("sort_order", "title"))
    index = next((i for i, a in enumerate(rows) if a.pk == article.pk), None)
    if index is None:
        return None, None
    return (rows[index - 1] if index > 0 else None), (rows[index + 1] if index + 1 < len(rows) else None)


def count_view(article):
    KBArticle.objects.filter(pk=article.pk).update(views=F("views") + 1)


# --- Staff management ----------------------------------------------------------------------------------

@transaction.atomic
def save_category(actor, category, *, name, description="", sort_order=0, is_active=True, icon="bi-folder", request=None):
    _require_manage(actor)
    created = category is None
    category = category or KBCategory()
    category.name, category.description = (name or "").strip(), (description or "").strip()
    category.sort_order, category.is_active = sort_order, is_active
    category.icon = re.sub(r"[^a-z0-9-]", "", (icon or "bi-folder").lower())[:40] or "bi-folder"
    if created:
        category.slug = unique_slug(KBCategory.objects.all(), category.name, max_length=60)
    category.full_clean()
    category.save()
    audit.record("kb_category.created" if created else "kb_category.updated", actor=actor, target=category,
                 metadata={"name": category.name}, request=request)
    return category


@transaction.atomic
def save_article(actor, article, *, category, title, body, is_published=False, sort_order=0, summary="", is_featured=False, request=None):
    _require_manage(actor)
    created = article is None
    article = article or KBArticle(created_by=actor)
    article.category, article.title = category, (title or "").strip()
    article.body, article.is_published, article.sort_order = (body or "").strip(), is_published, sort_order
    article.summary, article.is_featured = (summary or "").strip()[:220], bool(is_featured)
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


# --- What search engines read -------------------------------------------------------------------------------------------------------------------

def plain_text(text):
    """An article without its markup: for descriptions and for the FAQ answers search engines show."""
    out = re.sub(r"^\s*#{2,3} .*$", "", text or "", flags=re.M)
    out = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", out)
    out = re.sub(r"[*`>]|^\s*(?:[-*]|\d+[.)]) ", "", out, flags=re.M)
    return re.sub(r"\s+", " ", out).strip()


def faq_of(text):
    """[(question, answer)] from the section headed "Frequently asked questions": each ### line is a question, the text under it the answer."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    pairs, in_faq, question, answer = [], False, "", []
    for line in lines + ["## end"]:
        if line.startswith("## "):
            if question and answer:
                pairs.append((question, plain_text("\n".join(answer))))
            question, answer = "", []
            in_faq = line[3:].strip().lower().startswith("frequently asked")
        elif in_faq and line.startswith("### "):
            if question and answer:
                pairs.append((question, plain_text("\n".join(answer))))
            question, answer = line[4:].strip(), []
        elif in_faq and question:
            answer.append(line)
    return [(q, a) for q, a in pairs if a]


def description_of(article, limit=158):
    text = article.summary or plain_text(article.body)
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "\u2026"


def site_address(path):
    from django.conf import settings

    return settings.SITE_URL.rstrip("/") + path


def structured_data(article, site_name, faq):
    """The JSON-LD of an article page: the article, the breadcrumb trail and, when it has one, its FAQ. Returned as text that is safe inside a script element."""
    import json
    from django.urls import reverse

    url = site_address(reverse("help:article", args=[article.category.slug, article.slug]))
    blocks = [
        {"@context": SCHEMA, "@type": "Article", "headline": article.title[:110], "description": description_of(article), "url": url, "mainEntityOfPage": url,
         "datePublished": article.created_at.date().isoformat(), "dateModified": article.updated_at.date().isoformat(),
         "author": {"@type": "Organization", "name": site_name}, "publisher": {"@type": "Organization", "name": site_name}},
        {"@context": SCHEMA, "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Help centre", "item": site_address(reverse("help:index"))},
            {"@type": "ListItem", "position": 2, "name": article.category.name, "item": site_address(reverse("help:category", args=[article.category.slug]))},
            {"@type": "ListItem", "position": 3, "name": article.title, "item": url}]},
    ]
    if faq:
        blocks.append({"@context": SCHEMA, "@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq[:12]]})
    return json.dumps(blocks, ensure_ascii=False).replace("</", "<\\/")
