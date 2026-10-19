from django.shortcuts import render

from . import status


def status_page(request):
    response = render(request, "public/status.html", {"status": status.components()})
    response["Cache-Control"] = "public, max-age=60"  # the same for everyone: a minute at the edge is plenty
    return response
