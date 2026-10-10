"""Staff screen for native nodes (N2): add a node, show its one-time enrolment command once, see what each node reported, set its capacity, revoke it."""
from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError
from apps.products.models import Server, ServerKind

from . import backup_repo, enrollment, reputation, services
from .models import AgentNode, BackupRepository, NodeStatus


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


class RepositoryForm(forms.Form):
    endpoint = forms.CharField(max_length=300, help_text="The storage address, starting with https, for example s3.example.net")
    bucket = forms.CharField(max_length=63)
    prefix = forms.CharField(max_length=60, help_text="A folder in the bucket, one per node, for example node-1")
    access_key = forms.CharField(max_length=128, required=False, help_text="Leave blank to keep the saved key.")
    secret_key = forms.CharField(max_length=128, required=False, widget=forms.PasswordInput(render_value=False), help_text="Leave blank to keep the saved key.")
    password = forms.CharField(max_length=128, required=False, widget=forms.PasswordInput(render_value=False), help_text="Encrypts every backup; set once. Keep a copy offline.")
    object_lock = forms.BooleanField(required=False, label="The bucket is locked so this node cannot delete or overwrite its backups")


@portal_permission_required(perm("view_hosting"))
def backups(request):
    from apps.hosting.models import NativeBackup

    rows = []
    for node in AgentNode.objects.select_related("server").filter(status=NodeStatus.ENROLLED).order_by("server__name"):
        repo = BackupRepository.objects.filter(node=node).first()
        initial = {"endpoint": repo.endpoint, "bucket": repo.bucket, "prefix": repo.prefix, "object_lock": repo.object_lock_confirmed} if repo else {"prefix": node.server.name.lower().replace(" ", "-")}
        mine = NativeBackup.objects.filter(node=node)
        rows.append({"node": node, "repo": repo, "form": RepositoryForm(initial=initial), "complete": mine.filter(status="complete").count(), "failed": mine.filter(status="failed").count(),
                     "last_test": mine.exclude(restore_tested_at=None).order_by("-restore_tested_at").first(), "recent_failures": mine.filter(status="failed").order_by("-created_at")[:3]})
    return render(request, "agent/staff/backups.html", {"rows": rows, "can_manage": request.user.has_perm(perm("manage_hosting"))})


@require_POST
@portal_permission_required(perm("manage_hosting"))
def backup_save(request, pk):
    node = get_object_or_404(AgentNode, pk=pk, status=NodeStatus.ENROLLED)
    form = RepositoryForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Check the form: " + "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
        return redirect("agent_staff:backups")
    d = form.cleaned_data
    try:
        backup_repo.save_repository(request.user, node, endpoint=d["endpoint"], bucket=d["bucket"], prefix=d["prefix"], access_key=d["access_key"], secret_key=d["secret_key"],
                                    password=d["password"], object_lock=d["object_lock"], request=request)
        messages.success(request, "Saved. Press Apply to tell the node.")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect("agent_staff:backups")


@require_POST
@portal_permission_required(perm("manage_hosting"))
def backup_apply(request, pk):
    repo = get_object_or_404(BackupRepository.objects.select_related("node__server"), node_id=pk)
    try:
        backup_repo.apply(request.user, repo, request)
        messages.success(request, "Sent to the node. This page shows whether it accepted the settings.")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect("agent_staff:backups")


@require_POST
@portal_permission_required(perm("manage_hosting"))
def backup_node_config(request, pk):
    node = get_object_or_404(AgentNode.objects.select_related("server"), pk=pk, status=NodeStatus.ENROLLED)
    try:
        backup_repo.backup_node_config(request.user, node, request)
        messages.success(request, "Backing up the node's configuration.")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect("agent_staff:backups")


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
def node_reputation(request, pk):
    node = get_object_or_404(AgentNode.objects.select_related("server"), pk=pk, status="enrolled")
    result = reputation.check_node(node)
    messages.success(request, "No problems found." if result["ok"] else "There is a problem: see the mail reputation line.")
    return redirect("agent_staff:nodes")


@portal_permission_required(perm("view_hosting"))
def node_isolation(request, pk):
    """Ask the node to check itself against the isolation baseline and every account's installed limits. Read-only on the node."""
    node = get_object_or_404(AgentNode.objects.select_related("server"), pk=pk, status=NodeStatus.ENROLLED)
    try:
        report = services.call(node.server, "ISOLATION_CHECK", {}, wait=60, user=request.user)
        error = ""
    except ServiceError as exc:
        report, error = None, exc.message
    return render(request, "agent/staff/isolation.html", {"node": node, "report": report, "error": error})


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
