"""Put the starter help articles into the help centre. Safe to run again: it never changes an article or a category that is already there."""
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.support.kb_seed import ARTICLES, CATEGORIES
from apps.support.models import KBArticle, KBCategory


class Command(BaseCommand):
    help = "Add the starter help centre categories and articles that are missing."

    def handle(self, *args, **options):
        made, filled = {}, 0
        for position, (slug, name, icon, description) in enumerate(CATEGORIES):
            row = KBCategory.objects.filter(slug=slug).first() or KBCategory.objects.filter(name=name).first()
            if row is None:
                row = KBCategory.objects.create(slug=slug, name=name, icon=icon, description=description, sort_order=(position + 1) * 10, is_active=True)
            else:  # an empty starter category from before: give it its icon and words, change nothing else
                changed = False
                if row.icon == "bi-folder":
                    row.icon, changed = icon, True
                if not row.description:
                    row.description, changed = description, True
                if row.sort_order <= 7 and not row.articles.exists():
                    row.sort_order, changed = (position + 1) * 10, True
                if changed:
                    row.save()
            made[slug] = row
        for position, (category_slug, title, summary, featured, body) in enumerate(ARTICLES):
            category = made[category_slug]
            slug = slugify(title)[:80]
            if KBArticle.objects.filter(category=category, slug=slug).exists() or KBArticle.objects.filter(category=category, title=title).exists():
                continue
            KBArticle.objects.create(category=category, title=title, slug=slug, summary=summary, body=body.strip(), is_featured=featured, is_published=True, sort_order=(position + 1) * 10)
            filled += 1
        self.stdout.write(f"{len(made)} categories checked, {filled} articles added.")
