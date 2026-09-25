"""The public help centre (knowledgebase). Anyone can read published articles; nothing here changes data."""
from django.shortcuts import get_object_or_404, render

from . import forms, kb


def index(request):
    form = forms.KBSearchForm(request.GET or None)
    term = form.cleaned_data["q"] if form.is_valid() else ""
    return render(request, "support/kb/index.html", {
        "categories": kb.public_categories(), "form": form, "term": term,
        "results": kb.search_public(term).select_related("category") if term else None})


def category(request, slug):
    category = get_object_or_404(kb.public_categories(), slug=slug)
    return render(request, "support/kb/category.html", {"category": category,
                                                        "articles": kb.public_articles(category)})


def article(request, category_slug, slug):
    article = get_object_or_404(kb.public_articles().select_related("category"), category__slug=category_slug,
                                slug=slug)
    return render(request, "support/kb/article.html", {
        "article": article, "paragraphs": [p.strip() for p in article.body.replace("\r", "").split("\n\n") if p.strip()]})
