"""Staff support pages. Views only translate requests into service calls (which enforce every rule)."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.clients.models import Client
from apps.clients.services import search_clients
from apps.core import bulk
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError
from apps.core.web import ACTION_ERRORS, apply_form_error, query_id, run_action

from . import forms, kb, lifecycle, services
from .models import CannedReply, Department, KBArticle, KBCategory, Ticket

VIEW, MANAGE = perm("view_support"), perm("manage_support")


def _can_manage(request):
    return request.user.has_perm(MANAGE)


# --- Overview and lists ---------------------------------------------------------------------------------

@portal_permission_required(VIEW)
def overview(request):
    return render(request, "support/staff/overview.html", {**services.overview(request.user),
                                                           "can_manage": _can_manage(request), "section": "overview"})


@portal_permission_required(VIEW)
def ticket_list(request):
    form = forms.TicketFilterForm(request.GET or None)
    tickets = Ticket.objects.select_related("client", "department", "assigned_to")
    if form.is_valid():
        data = form.cleaned_data
        tickets = services.search_tickets(tickets, data["q"])
        if data["status"] == "active":
            tickets = tickets.filter(status__in=lifecycle.ACTIVE)
        elif data["status"]:
            tickets = tickets.filter(status=data["status"])
        if data["department"]:
            tickets = tickets.filter(department=data["department"])
        if data["priority"]:
            tickets = tickets.filter(priority=data["priority"])
        if data["assigned"] == "me":
            tickets = tickets.filter(assigned_to=request.user)
        elif data["assigned"] == "none":
            tickets = tickets.filter(assigned_to__isnull=True)
    page = Paginator(tickets.order_by("-last_activity_at", "-id"), 25).get_page(request.GET.get("page"))
    return render(request, "support/staff/list.html", {"form": form, "page": page, "section": "tickets",
                                                       "can_manage": _can_manage(request)})


@require_POST
@portal_permission_required(MANAGE)
def ticket_bulk(request):
    """"With selected": assign to me, mark resolved, close, or set the priority of every ticked ticket."""
    do = request.POST.get("do", "")
    actor = request.user
    actions = {
        "assign_me": ("assigned to you", lambda t: services.assign(actor, t, actor, request=request)),
        "resolve": ("marked resolved", lambda t: services.set_status(actor, t, "resolved", request=request)),
        "close": ("closed", lambda t: services.set_status(actor, t, "closed", request=request)),
        "priority": ("re-prioritised", lambda t: services.set_priority(actor, t, request.POST.get("priority", ""),
                                                                       request=request)),
    }
    if do not in actions:
        messages.error(request, "Choose what to do with the selected tickets.")
    else:
        verb, action = actions[do]
        bulk.run(request, Ticket.objects.all(), bulk.selected_ids(request), action, verb=f"ticket(s) {verb}",
                 label=lambda t: t.reference)
    return bulk.back_to(request, "support_staff:tickets")


@portal_permission_required(MANAGE)
def ticket_new(request):
    client_id = query_id(request, "client")
    if client_id is None:
        term = request.GET.get("q", "").strip()
        clients = search_clients(Client.objects.all(), term).order_by("company_name", "first_name", "id")[:15] if term else []
        return render(request, "billing/staff/client_picker.html", {
            "title": "Tickets", "create_url": "support_staff:ticket_new", "clients": clients, "q": term})
    client = get_object_or_404(Client, pk=client_id)
    form = forms.NewTicketForm(request.POST or None, request.FILES or None, clients=[client], staff=True)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            ticket = services.open_ticket(
                request.user, client, department=data["department"], subject=data["subject"], body=data["body"],
                priority=data["priority"], hosting_account=data["hosting_account"], domain=data["domain"],
                files=data["files"], request=request)
        except ACTION_ERRORS as exc:
            apply_form_error(form, exc)
        else:
            messages.success(request, f"Ticket {ticket.reference} opened for {client.display_name}.")
            return redirect("support_staff:ticket", pk=ticket.pk)
    return render(request, "support/staff/new.html", {"form": form, "client": client, "section": "tickets"})


# --- One ticket -----------------------------------------------------------------------------------------------

def _ticket_page(request, ticket, reply_form):
    return render(request, "support/staff/detail.html", {
        "ticket": ticket, "messages_": services.messages_for(request.user, ticket), "reply_form": reply_form,
        "assign_form": forms.AssignForm(initial={"agent": ticket.assigned_to_id or ""}),
        "priority_form": forms.PriorityForm(initial={"priority": ticket.priority}),
        "department_form": forms.DepartmentChangeForm(initial={"department": ticket.department_id}),
        "status_form": forms.StatusForm(initial={"status": ticket.status}),
        "can_manage": _can_manage(request), "section": "tickets"})


@portal_permission_required(VIEW)
def ticket_detail(request, pk):
    ticket = get_object_or_404(Ticket.objects.select_related("client", "department", "assigned_to", "opened_by",
                                                              "hosting_account", "domain"), pk=pk)
    reply_form = forms.StaffReplyForm(department=ticket.department)
    if request.method == "POST":
        if not _can_manage(request):
            raise PermissionDenied
        reply_form = forms.StaffReplyForm(request.POST, request.FILES, department=ticket.department)
        if "insert" in request.POST:  # put a predefined reply into the box for the agent to review - nothing is sent
            reply_form.is_valid()
            canned = reply_form.cleaned_data.get("canned")
            if canned is not None:
                existing = (reply_form.data.get("body") or "").strip()
                text = services.render_canned(canned, ticket, request.user)
                reply_form = forms.StaffReplyForm(department=ticket.department, initial={
                    "body": f"{existing}\n\n{text}".strip(), "internal": reply_form.data.get("internal") == "on",
                    "set_status": reply_form.data.get("set_status", "")})
            else:
                messages.error(request, "Choose a predefined reply to insert.")
                reply_form = forms.StaffReplyForm(department=ticket.department, initial={
                    "body": reply_form.data.get("body", "")})
        elif reply_form.is_valid():
            data = reply_form.cleaned_data
            try:
                if not (data["body"] or "").strip():
                    raise ServiceError("Enter a reply.")
                services.reply(request.user, ticket, data["body"], files=data["files"], internal=data["internal"],
                               set_status=data["set_status"] or None, request=request)
            except ACTION_ERRORS as exc:
                apply_form_error(reply_form, exc)
            else:
                messages.success(request, "Note added." if data["internal"] else "Reply sent.")
                return redirect("support_staff:ticket", pk=ticket.pk)
    return _ticket_page(request, ticket, reply_form)


def _ticket_action(request, pk, function, message):
    ticket = get_object_or_404(Ticket, pk=pk)

    def action():
        function(ticket)
        return message

    return run_action(request, action, "support_staff:ticket", pk=pk)


@require_POST
@portal_permission_required(MANAGE)
def ticket_assign(request, pk):
    form = forms.AssignForm(request.POST)

    def go(ticket):
        if not form.is_valid():
            raise ServiceError("Choose an agent.")
        services.assign(request.user, ticket, form.selected(), request=request)

    return _ticket_action(request, pk, go, "Assignment updated.")


@require_POST
@portal_permission_required(MANAGE)
def ticket_take(request, pk):
    return _ticket_action(request, pk, lambda t: services.assign(request.user, t, request.user, request=request),
                          "The ticket is yours.")


@require_POST
@portal_permission_required(MANAGE)
def ticket_priority(request, pk):
    form = forms.PriorityForm(request.POST)

    def go(ticket):
        if not form.is_valid():
            raise ServiceError("Choose a valid priority.")
        services.set_priority(request.user, ticket, form.cleaned_data["priority"], request=request)

    return _ticket_action(request, pk, go, "Priority updated.")


@require_POST
@portal_permission_required(MANAGE)
def ticket_department(request, pk):
    form = forms.DepartmentChangeForm(request.POST)

    def go(ticket):
        if not form.is_valid():
            raise ServiceError("Choose a department.")
        services.set_department(request.user, ticket, form.cleaned_data["department"], request=request)

    return _ticket_action(request, pk, go, "Department updated.")


@require_POST
@portal_permission_required(MANAGE)
def ticket_status(request, pk):
    form = forms.StatusForm(request.POST)

    def go(ticket):
        if not form.is_valid():
            raise ServiceError("Choose a valid status.")
        services.set_status(request.user, ticket, form.cleaned_data["status"], reason="set by staff", request=request)

    return _ticket_action(request, pk, go, "Status updated.")


# --- Departments and predefined replies --------------------------------------------------------------------------

@portal_permission_required(VIEW)
def departments(request):
    edit = Department.objects.filter(pk=request.GET.get("edit") or 0).first()
    initial = {}
    if edit:
        initial = {"name": edit.name, "description": edit.description, "sort_order": edit.sort_order,
                   "is_active": edit.is_active, "default_assignee": edit.default_assignee_id or ""}
    return render(request, "support/staff/departments.html", {
        "departments": Department.objects.all(), "form": forms.DepartmentForm(initial=initial) if _can_manage(request) else None,
        "edit": edit, "can_manage": _can_manage(request), "section": "departments"})


@require_POST
@portal_permission_required(MANAGE)
def department_save(request):
    form = forms.DepartmentForm(request.POST)
    target = Department.objects.filter(pk=request.POST.get("id") or 0).first()

    def action():
        if not form.is_valid():
            raise ServiceError("Enter a valid department: " + "; ".join(f"{f}: {e[0]}" for f, e in form.errors.items()))
        data = form.cleaned_data
        services.save_department(request.user, target, name=data["name"], description=data["description"],
                                 is_active=data["is_active"], sort_order=data["sort_order"],
                                 default_assignee=form.assignee(), request=request)
        return "Department saved."

    return run_action(request, action, "support_staff:departments")


@portal_permission_required(VIEW)
def replies(request):
    edit = CannedReply.objects.filter(pk=request.GET.get("edit") or 0).first()
    initial = {}
    if edit:
        initial = {"title": edit.title, "body": edit.body, "department": edit.department_id,
                   "sort_order": edit.sort_order, "is_active": edit.is_active}
    return render(request, "support/staff/replies.html", {
        "replies": CannedReply.objects.select_related("department"),
        "form": forms.CannedReplyForm(initial=initial) if _can_manage(request) else None, "edit": edit,
        "can_manage": _can_manage(request), "section": "replies"})


@require_POST
@portal_permission_required(MANAGE)
def reply_save(request):
    form = forms.CannedReplyForm(request.POST)
    target = CannedReply.objects.filter(pk=request.POST.get("id") or 0).first()

    def action():
        if not form.is_valid():
            raise ServiceError("Enter a valid reply: " + "; ".join(f"{f}: {e[0]}" for f, e in form.errors.items()))
        data = form.cleaned_data
        services.save_canned_reply(request.user, target, title=data["title"], body=data["body"],
                                   department=data["department"], is_active=data["is_active"],
                                   sort_order=data["sort_order"], request=request)
        return "Predefined reply saved."

    return run_action(request, action, "support_staff:replies")


# --- Knowledgebase management ----------------------------------------------------------------------------------------

@portal_permission_required(VIEW)
def kb_manage(request):
    category = KBCategory.objects.filter(pk=request.GET.get("category") or 0).first()
    article = KBArticle.objects.filter(pk=request.GET.get("article") or 0).select_related("category").first()
    can = _can_manage(request)
    category_initial = {"name": category.name, "description": category.description,
                        "sort_order": category.sort_order, "is_active": category.is_active} if category else {}
    article_initial = {"category": article.category_id, "title": article.title, "body": article.body,
                       "sort_order": article.sort_order, "is_published": article.is_published} if article else {}
    return render(request, "support/staff/kb.html", {
        "categories": KBCategory.objects.all(),
        "articles": KBArticle.objects.select_related("category").order_by("category__sort_order", "sort_order", "title"),
        "category_form": forms.KBCategoryForm(initial=category_initial) if can else None,
        "article_form": forms.KBArticleForm(initial=article_initial) if can else None,
        "edit_category": category, "edit_article": article, "can_manage": can, "section": "kb"})


@require_POST
@portal_permission_required(MANAGE)
def kb_category_save(request):
    form = forms.KBCategoryForm(request.POST)
    target = KBCategory.objects.filter(pk=request.POST.get("id") or 0).first()

    def action():
        if not form.is_valid():
            raise ServiceError("Enter a valid category.")
        data = form.cleaned_data
        kb.save_category(request.user, target, name=data["name"], description=data["description"],
                         sort_order=data["sort_order"], is_active=data["is_active"], request=request)
        return "Category saved."

    return run_action(request, action, "support_staff:kb")


@require_POST
@portal_permission_required(MANAGE)
def kb_article_save(request):
    form = forms.KBArticleForm(request.POST)
    target = KBArticle.objects.filter(pk=request.POST.get("id") or 0).first()

    def action():
        if not form.is_valid():
            raise ServiceError("Enter a valid article: " + "; ".join(f"{f}: {e[0]}" for f, e in form.errors.items()))
        data = form.cleaned_data
        kb.save_article(request.user, target, category=data["category"], title=data["title"], body=data["body"],
                        is_published=data["is_published"], sort_order=data["sort_order"], request=request)
        return "Article saved."

    return run_action(request, action, "support_staff:kb")


@require_POST
@portal_permission_required(MANAGE)
def kb_article_delete(request, pk):
    article = get_object_or_404(KBArticle, pk=pk)

    def action():
        kb.delete_article(request.user, article, request=request)
        return "Article deleted."

    return run_action(request, action, "support_staff:kb")

