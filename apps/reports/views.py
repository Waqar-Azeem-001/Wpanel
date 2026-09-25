"""Server-rendered reports and downloads. Every rule lives in ``services``."""
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import render

from apps.accounts.roles import perm
from apps.core.exceptions import ServiceError
from apps.core.web import ACTION_ERRORS, error_text

from . import exports, services
from .definitions import GROUPS, SCOPES, describe, parse_params, query_for
from .exports import display, is_number


def _login_and_permission(request):
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    if not request.user.has_perm(perm("view_reports")):
        raise PermissionDenied
    return None


def index(request):
    denied = _login_and_permission(request)
    if denied:
        return denied
    return render(request, "reports/index.html", {
        "groups": services.grouped(request.user), "headline": [
            (label, display(kind, value), slug) for label, kind, value, slug in services.headline(request.user)]})


def _download(request, slug, fmt):
    content, content_type, filename = services.export(request.user, slug, request.GET, fmt, request=request)
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


def detail(request, slug):
    denied = _login_and_permission(request)
    if denied:
        return denied
    try:
        definition = services.get_definition(slug)
    except ServiceError:
        raise Http404("There is no such report.")
    if not services.can_run(request.user, definition):
        raise PermissionDenied
    context = {"definition": definition, "query": request.GET, "groups": GROUPS, "scopes": SCOPES}
    try:
        if request.GET.get("export"):
            return _download(request, slug, request.GET["export"])
        _, params, result = services.run(request.user, slug, request.GET)
    except ACTION_ERRORS as exc:  # a bad date or number: say so and show the form again
        messages.error(request, error_text(exc))
        context["params"] = parse_params(definition, {})
        return render(request, "reports/report.html", context)
    rows, cut = services.screen_rows(result)
    context.update({
        "params": params, "shown": describe(definition, params), "result": result,
        "export_query": query_for(definition, params),
        "columns": [(c.label, is_number(c.kind)) for c in result.columns],
        "table": [[(display(c.kind, row.get(c.key)), is_number(c.kind)) for c in result.columns] for row in rows],
        "summary": [(label, display(kind, value)) for label, kind, value in result.summary],
        "cut": cut, "total_rows": len(result.rows), "formats": list(exports.EXPORT_TYPES)})
    return render(request, "reports/report.html", context)
