"""The public help centre (knowledgebase). Anyone can read published articles; reading only counts a view."""
from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.html import escape

from . import forms, kb


def index(request):
    form = forms.KBSearchForm(request.GET or None)
    term = form.cleaned_data["q"] if form.is_valid() else ""
    results = list(kb.search_public(term).select_related("category")[:40]) if term else None
    return render(request, "support/kb/index.html", {
        "canonical": kb.site_address(reverse("help:index")), "noindex": bool(term),
        "categories": kb.public_categories(), "form": form, "term": term, "results": results,
        "featured": list(kb.featured()) if not term else [], "popular": list(kb.popular()) if not term else []})


def category(request, slug):
    category = get_object_or_404(kb.public_categories(), slug=slug)
    articles = list(kb.public_articles(category).order_by("sort_order", "title"))
    for a in articles:
        a.minutes = kb.reading_minutes(a.body)
    return render(request, "support/kb/category.html", {"category": category, "articles": articles, "others": kb.public_categories().exclude(pk=category.pk),
                                                        "canonical": kb.site_address(reverse("help:category", args=[category.slug]))})


def article(request, category_slug, slug):
    article = get_object_or_404(kb.public_articles().select_related("category"), category__slug=category_slug, slug=slug)
    kb.count_view(article)
    html, contents = kb.render_body(article.body)
    before, after = kb.neighbours(article)
    faq = kb.faq_of(article.body)
    return render(request, "support/kb/article.html", {
        "topic": list(kb.public_articles(article.category).order_by("sort_order", "title")), "voted": request.COOKIES.get(f"kbvote_{article.pk}", ""),
        "description": kb.description_of(article), "canonical": kb.site_address(reverse("help:article", args=[article.category.slug, article.slug])),
        "json_ld": kb.structured_data(article, getattr(request, "site_name", "") or settings.SITE_NAME, faq), "faq": faq,
        "article": article, "body": html, "contents": contents, "minutes": kb.reading_minutes(article.body), "related": kb.related(article), "before": before, "after": after,
        "others": kb.public_categories().exclude(pk=article.category_id)})


def vote(request, category_slug, slug):
    """"Did this answer your question?": one vote per reader per article (remembered in a cookie), so staff can see which articles help."""
    from django.http import Http404, JsonResponse
    from django.db.models import F
    from .models import KBArticle

    if request.method != "POST":
        raise Http404
    article = get_object_or_404(kb.public_articles(), category__slug=category_slug, slug=slug)
    choice = request.POST.get("choice")
    if choice not in ("yes", "no"):
        return JsonResponse({"ok": False}, status=400)
    cookie = f"kbvote_{article.pk}"
    if not request.COOKIES.get(cookie):
        KBArticle.objects.filter(pk=article.pk).update(**{"helpful_yes" if choice == "yes" else "helpful_no": F("helpful_yes" if choice == "yes" else "helpful_no") + 1})
    response = JsonResponse({"ok": True, "choice": request.COOKIES.get(cookie) or choice})
    response.set_cookie(cookie, request.COOKIES.get(cookie) or choice, max_age=60 * 60 * 24 * 30, samesite="Lax", httponly=True)
    return response


SITEMAP_NS = "http" + "://www.sitemaps.org/schemas/sitemap/0.9"


def sitemap(request):
    """sitemap.xml: the help centre's own pages, with the day each was last changed."""
    rows = [(kb.site_address(reverse("help:index")), None)]
    rows += [(kb.site_address(reverse("help:category", args=[c.slug])), None) for c in kb.public_categories()]
    rows += [(kb.site_address(reverse("help:article", args=[a.category.slug, a.slug])), a.updated_at.date().isoformat()) for a in kb.public_articles().order_by("category__sort_order", "sort_order", "title")]
    body = "".join(f"<url><loc>{escape(url)}</loc>" + (f"<lastmod>{day}</lastmod>" if day else "") + "</url>" for url, day in rows)
    xml = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="' + SITEMAP_NS + '">' + body + "</urlset>"
    response = HttpResponse(xml, content_type="application/xml")
    response["Cache-Control"] = "public, max-age=3600"
    return response


def robots(request):
    """robots.txt: the public pages may be read, the signed-in areas and the API may not; the sitemap is named."""
    text = "User-agent: *\nDisallow: /account/\nDisallow: /staff/\nDisallow: /api/\nDisallow: /admin/\nDisallow: /_preview/\nAllow: /\n\nSitemap: " + kb.site_address("/sitemap.xml") + "\n"
    response = HttpResponse(text, content_type="text/plain")
    response["Cache-Control"] = "public, max-age=3600"
    return response
