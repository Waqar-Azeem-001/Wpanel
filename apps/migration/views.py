"""Staff screens for moving cPanel accounts to native nodes (M3) and for deciding when cPanel can be retired (M4)."""
from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.agent.models import AgentNode, NodeStatus
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError
from apps.hosting.models import LIVE_STATUSES, HostingAccount
from apps.products.models import Server, ServerKind

from . import readiness, services
from .models import ACTIVE, Migration, MigrationState as S


def _nodes(capability):
    ids = [n.server_id for n in AgentNode.objects.filter(status=NodeStatus.ENROLLED) if capability in (n.capabilities or [])]
    return Server.objects.filter(pk__in=ids, kind=ServerKind.NATIVE).order_by("name")


class StartForm(forms.Form):
    account = forms.ModelChoiceField(HostingAccount.objects.none(), help_text="Live accounts that are still on a cPanel server.")
    target = forms.ModelChoiceField(Server.objects.none(), label="Web node")
    mail_target = forms.ModelChoiceField(Server.objects.none(), required=False, label="Mail node", help_text="Leave empty to leave mail out of this migration.")
    window_days = forms.IntegerField(min_value=1, max_value=60, initial=7, label="Rollback window (days)")
    expected_sha256 = forms.CharField(max_length=64, required=False, label="Backup checksum", help_text="Optional: the SHA-256 printed on the cPanel server. The node refuses a backup that differs.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        busy = Migration.objects.filter(state__in=[s.value for s in ACTIVE]).values_list("account_id", flat=True)
        self.fields["account"].queryset = (HostingAccount.objects.filter(status__in=LIVE_STATUSES, server__kind=ServerKind.WHM_API).exclude(pk__in=busy)
                                           .select_related("server").order_by("domain"))
        self.fields["target"].queryset = _nodes("web")
        self.fields["mail_target"].queryset = _nodes("mail")


@portal_permission_required(perm("view_hosting"))
def dashboard(request):
    state = request.GET.get("state", "")
    rows = Migration.objects.select_related("account", "target", "source")
    if state in S.values:
        rows = rows.filter(state=state)
    rows = list(rows[:200])
    for m in rows:
        m.marks = services.stage_marks(m)
    return render(request, "migration/staff/dashboard.html", {"summary": services.summary(), "rows": rows, "form": StartForm(), "state": state, "states": S.choices,
                                                              "can_manage": request.user.has_perm(perm("manage_hosting"))})


@require_POST
@portal_permission_required(perm("manage_hosting"))
def start(request):
    form = StartForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose an account and a web node. " + " ".join(f"{k}: {', '.join(v)}" for k, v in form.errors.items()))
        return redirect("migration_staff:dashboard")
    try:
        m = services.start(request.user, form.cleaned_data["account"], form.cleaned_data["target"], mail_target=form.cleaned_data["mail_target"],
                           window_days=form.cleaned_data["window_days"], expected_sha256=form.cleaned_data["expected_sha256"].strip(), request=request)
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect("migration_staff:dashboard")
    messages.success(request, "Migration started. Make the cPanel backup and copy it to the node, then press Check backup.")
    return redirect("migration_staff:detail", pk=m.pk)


@portal_permission_required(perm("view_hosting"))
def detail(request, pk):
    m = get_object_or_404(Migration.objects.select_related("account", "source", "target", "mail_target"), pk=pk)
    return render(request, "migration/staff/detail.html", {"m": m, "marks": services.stage_marks(m), "events": m.events.all(), "how": services.instructions(m),
                                                          "can_manage": request.user.has_perm(perm("manage_hosting")), "S": S})


ACTIONS = {"check": lambda r, m: services.check_archive(r.user, m), "approve": lambda r, m: services.approve(r.user, m, accept_warnings=bool(r.POST.get("accept"))),
           "retry": lambda r, m: services.retry(r.user, m), "cancel": lambda r, m: services.cancel(r.user, m), "site": lambda r, m: services.check_site(m),
           "cutover": lambda r, m: services.cutover(r.user, m, request=r), "rollback": lambda r, m: services.rollback(r.user, m, request=r),
           "finish": lambda r, m: services.finish(r.user, m, force=bool(r.POST.get("force")))}


@require_POST
@portal_permission_required(perm("manage_hosting"))
def act(request, pk, action):
    m = get_object_or_404(Migration, pk=pk)
    if action not in ACTIONS:
        messages.error(request, "Unknown action.")
        return redirect("migration_staff:detail", pk=pk)
    try:
        ACTIONS[action](request, m)
        messages.success(request, "Done.")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect("migration_staff:detail", pk=pk)


@portal_permission_required(perm("view_hosting"))
def retirement(request):
    return render(request, "migration/staff/retirement.html", {"checks": readiness.checks(), "summary": services.summary()})


@require_POST
@portal_permission_required(perm("manage_hosting"))
def signoff(request, key):
    try:
        if request.POST.get("withdraw"):
            readiness.withdraw(request.user, key)
        else:
            readiness.sign(request.user, key, request.POST.get("note", ""))
        messages.success(request, "Saved.")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect("migration_staff:retirement")
