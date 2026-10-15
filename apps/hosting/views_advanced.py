"""
Advanced tools: one page per tool under the website's Advanced group. The work is in ``site_tools`` (rules in .htaccess), ``server_tools`` (one-time server tools) and the audit
trail; this reads the choices, calls them, and says in plain words what happened.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.audit.models import AuditEvent
from apps.core import portal
from apps.core.exceptions import ServiceError

from . import control, server_tools, site_tools
from .adapters.capabilities import Capability
from .views import _own_account

TOOLS = {
    "protect": ("Password protect directories", "bi-lock"),
    "hotlink": ("Hotlink protection", "bi-link-45deg"),
    "indexes": ("Folder index manager", "bi-folder-symlink"),
    "ips": ("IP manager", "bi-slash-circle"),
    "phpinfo": ("PHP info", "bi-info-square"),
    "permissions": ("Fix file permissions", "bi-wrench-adjustable"),
    "activity": ("Activity log", "bi-clock-history"),
}
NEEDS_FILES = set(TOOLS) - {"activity"}

ACTION_WORDS = {
    "hosting.control": "Changed a setting", "hosting.site_ready": "Website created", "hosting.site_failed": "Website could not be created",
    "hosting.server_tool": "Ran a server tool", "hosting.wordpress_action": "Changed WordPress", "hosting.website_deleted": "Website deleted",
}


def _files_ok(account):
    try:
        return "files" in control.capabilities_for(account)
    except ServiceError:
        return False


def _folders(user, account):
    """The website folder and the folders directly inside it, for the pickers."""
    found = ["public_html"]
    try:
        for entry in control.perform(user, account, Capability.FILES, "list_files", "public_html"):
            if entry.is_dir and not entry.name.startswith(".") and entry.name not in ("cgi-bin", "wp-admin", "wp-includes"):
                found.append(f"public_html/{entry.name}")
    except ServiceError:
        pass
    return found


def _activity(request, account):
    rows = []
    for event in AuditEvent.objects.filter(target_type="hosting.hostingaccount", target_id=str(account.pk)).order_by("-created_at")[:100]:
        mine = event.actor_id == request.user.pk
        who = "You" if mine else ("Hosting team" if event.actor_id else "WHPanel")
        words = next((v for k, v in ACTION_WORDS.items() if event.action.startswith(k)), event.action.replace("_", " ").replace(".", " "))
        rows.append({"when": event.created_at, "who": who, "what": words[:1].upper() + words[1:], "action": event.action})
    return rows


@login_required
def advanced_tool(request, pk, tool):
    if tool not in TOOLS:
        raise Http404
    account = _own_account(request, pk)
    title, icon = TOOLS[tool]
    context = {"account": account, "sidebar": portal.service_sidebar(request, account), "tool": tool, "title": title, "icon": icon,
               "do_url": reverse("hosting_customer:advanced_do", args=[account.pk]), "supported": True, "page_url": reverse("hosting_customer:advanced", args=[account.pk, tool])}
    if tool in NEEDS_FILES and not _files_ok(account):
        context["supported"] = False
    elif tool in ("protect", "hotlink", "indexes", "ips"):
        try:
            context["state"] = site_tools.state(request.user, account)
        except ServiceError as exc:
            messages.error(request, exc.message)
            context["state"] = {"protect": [], "indexes": [], "hotlink": {}, "ips": []}
        context["folders"] = _folders(request.user, account) if tool in ("protect", "indexes") else []
        context["hotlink_types"] = site_tools.HOTLINK_TYPES
        context["your_ip"] = request.META.get("REMOTE_ADDR", "")
        if tool == "hotlink":
            chosen = context["state"].get("hotlink") or {}
            context["hot_on"] = bool(chosen.get("enabled"))
            context["hot_domains"] = ", ".join(d for d in chosen.get("domains", []) if d != account.domain.lower())
            context["hot_types"] = chosen.get("types") or ["jpg", "jpeg", "png", "gif", "webp"]
    elif tool == "phpinfo" and request.GET.get("load") == "1":
        try:
            context["info"] = server_tools.run(request.user, account, "phpinfo")
        except ServiceError as exc:
            context["error"] = exc.message
    elif tool == "activity":
        context["events"] = _activity(request, account)
    return render(request, "hosting/customer/advanced.html", context)


@require_POST
@login_required
def advanced_do(request, pk):
    account = _own_account(request, pk)
    post = request.POST
    action, tool = post.get("action", ""), post.get("tool", "")
    try:
        if action == "protect":
            site_tools.protect_folder(request.user, account, post.get("path", ""), post.get("login", ""), post.get("password", ""))
            messages.success(request, "The folder now asks for a username and password.")
        elif action == "unprotect":
            site_tools.unprotect_folder(request.user, account, post.get("path", ""))
            messages.success(request, "The folder is open again.")
        elif action == "remove_user":
            site_tools.remove_protect_user(request.user, account, post.get("path", ""), post.get("login", ""))
            messages.success(request, "User removed.")
        elif action == "hotlink_on":
            site_tools.set_hotlink(request.user, account, True, [d for d in post.get("domains", "").replace(",", " ").split() if d], post.getlist("types"))
            messages.success(request, "Other websites can no longer show your files directly.")
        elif action == "hotlink_off":
            site_tools.set_hotlink(request.user, account, False)
            messages.success(request, "Hotlink protection is off.")
        elif action == "listing":
            site_tools.set_listing(request.user, account, post.get("path", ""), post.get("value") == "show")
            messages.success(request, "Saved.")
        elif action == "listing_reset":
            site_tools.reset_listing(request.user, account, post.get("path", ""))
            messages.success(request, "Back to the server's default for that folder.")
        elif action == "block_ip":
            site_tools.block_ip(request.user, account, post.get("ip", ""), post.get("note", ""), request.META.get("REMOTE_ADDR", ""))
            messages.success(request, "That address is blocked.")
        elif action == "unblock_ip":
            site_tools.unblock_ip(request.user, account, post.get("ip", ""))
            messages.success(request, "That address can open your website again.")
        elif action == "permissions":
            result = server_tools.run(request.user, account, "permissions")
            messages.success(request, f"Checked {result['folders']} folders and {result['files']} files; {result['fixed']} were put back to safe permissions."
                             + ("" if result.get("complete", True) else " The website is very large, so run it again to finish."))
            tool = "permissions"
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect("hosting_customer:advanced", pk=account.pk, tool=tool if tool in TOOLS else "protect")
