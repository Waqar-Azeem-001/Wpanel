"""
Old and vanity addresses (roadmap Rule 5.7): every path that has ever been shown to a person keeps working.

A path is never removed. When a page moves, its URL name stays and the old path is listed here, answering 301 with the
query string kept. ``deep`` entries also forward what follows the prefix, so ``/store/starter/`` reaches ``/products/starter/``.
"""
from django.urls import path, reverse
from django.views.generic import RedirectView

# (old path, name of the current page, forward the rest of the path?)
RETIRED = [
    ("login/", "accounts:login", False),
    ("register/", "accounts:register", False),
    ("store/", "catalog:product_list", True),
    ("knowledgebase/", "help:index", True),
]


class RetiredPath(RedirectView):
    permanent = True
    target_name = ""

    def get_redirect_url(self, *args, **kwargs):
        rest = kwargs.get("rest", "")
        url = reverse(self.target_name) + rest
        return url + ("?" + self.request.META["QUERY_STRING"] if self.request.META.get("QUERY_STRING") else "")


def retired_urlpatterns():
    patterns = []
    for old, name, deep in RETIRED:
        view = RetiredPath.as_view(target_name=name)
        route = f"{old}<path:rest>" if deep else old
        patterns.append(path(route, view, name="retired_" + old.strip("/").replace("/", "_") + ("_deep" if deep else "")))
        if deep:
            patterns.append(path(old, view, name="retired_" + old.strip("/").replace("/", "_")))
    return patterns
