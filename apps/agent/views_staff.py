"""Staff screen for native nodes (N2): add a node, show its one-time enrolment command once, see what each node reported, set its capacity, revoke it."""
from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError
from apps.products.models import Server, ServerKind

from . import enrollment
from .models import AgentNode


class NewNodeForm(forms.Form):
    name = forms.CharField(max_length=100)
    hostname = forms.CharField(max_length=255, help_text="The server's public name, for example node1.example.com.")
    region = forms.CharField(max_length=60, required=False)


def _rows():
    rows = []
    for node in AgentNode.objects.select_related("server").order_by("server__name"):
        rows.append({"node": node, "state": enrollment.health_state(node), "services": (node.health or {}).get("services", {})})
    return rows


def _page(request, form=None, secret=None):
    return render(request, "agent/staff/nodes.html", {"rows": _rows(), "form": form or NewNodeForm(), "secret": secret,
                                                       "can_manage": request.user.has_perm(perm("manage_hosting"))})


@portal_permission_required(perm("view_hosting"))
def nodes(request):
    return _page(request)


@require_POST
@portal_permission_required(perm("manage_hosting"))
def node_add(request):
    form = NewNodeForm(request.POST)
    if not form.is_valid():
        return _page(request, form)
    data = form.cleaned_data
    if Server.objects.filter(name=data["name"]).exists():
        form.add_error("name", "A server with this name already exists.")
        return _page(request, form)
    server = Server.objects.create(name=data["name"], hostname=data["hostname"], region=data["region"], kind=ServerKind.NATIVE)
    node, token = enrollment.invite(request.user, server, request)
    return _page(request, secret={"node": node, "token": token})  # shown in this response only; it is not stored in readable form


@require_POST
@portal_permission_required(perm("manage_hosting"))
def node_invite(request, pk):
    node = get_object_or_404(AgentNode, pk=pk)
    try:
        node, token = enrollment.invite(request.user, node.server, request)
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect("agent_staff:nodes")
    return _page(request, secret={"node": node, "token": token})


@require_POST
@portal_permission_required(perm("manage_hosting"))
def node_revoke(request, pk):
    enrollment.revoke(request.user, get_object_or_404(AgentNode, pk=pk), request)
    messages.success(request, "The node was revoked. Its certificate and token no longer work.")
    return redirect("agent_staff:nodes")


@require_POST
@portal_permission_required(perm("manage_hosting"))
def node_capacity(request, pk):
    node = get_object_or_404(AgentNode, pk=pk)
    raw = request.POST.get("capacity_sites", "").strip()
    if raw and (not raw.isdigit() or int(raw) > 100000):
        messages.error(request, "Capacity must be a whole number.")
    else:
        node.capacity_sites = int(raw) if raw else None
        node.save(update_fields=["capacity_sites", "updated_at"])
        messages.success(request, "Capacity saved.")
    return redirect("agent_staff:nodes")
