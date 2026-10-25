from django.shortcuts import render

from . import status


def status_page(request):
    response = render(request, "public/status.html", {"status": status.components()})
    response["Cache-Control"] = "public, max-age=10"  # the same for everyone; short, because the page says how old the figures are
    return response
