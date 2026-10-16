"""Setup > AI Agent > Knowledge: the entries the Agent answers from, and the questions it could not answer."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.audit import services as audit

from .models import KnowledgeEntry, Unanswered


def _may(request, manage=False):
    if not request.user.has_perm(perm("manage_settings" if manage else "view_settings")):
        raise PermissionDenied


@login_required
def knowledge(request):
    _may(request)
    return render(request, "assistant/knowledge.html", {"entries": KnowledgeEntry.objects.all(), "gaps": Unanswered.objects.all()[:40], "can_manage": request.user.has_perm(perm("manage_settings"))})


@login_required
def entry_form(request, pk=None):
    _may(request)
    can_manage = request.user.has_perm(perm("manage_settings"))
    entry = get_object_or_404(KnowledgeEntry, pk=pk) if pk else KnowledgeEntry()
    values = {"title": entry.title, "keywords": entry.keywords, "answer": entry.answer, "action": entry.action, "is_active": entry.is_active if pk else True,
              "sort_order": entry.sort_order if pk else 100}
    if not pk and request.GET.get("q"):
        values["keywords"] = request.GET["q"][:300]
        values["title"] = request.GET["q"][:60]
    if request.method == "POST":
        _may(request, manage=True)
        post = request.POST
        values = {"title": post.get("title", "").strip(), "keywords": "\n".join(l.strip() for l in post.get("keywords", "").splitlines() if l.strip()), "answer": post.get("answer", "").strip(),
                  "action": post.get("action", ""), "is_active": post.get("is_active") == "on", "sort_order": post.get("sort_order") or 100}
        try:
            if not values["title"] or not values["keywords"] or not values["answer"]:
                raise ValidationError("Give the entry a title, at least one way customers ask it, and the answer.")
            if values["action"] not in dict(KnowledgeEntry.ACTIONS):
                raise ValidationError("Choose one of the listed actions.")
            entry.title, entry.keywords, entry.answer, entry.action = values["title"][:120], values["keywords"][:3000], values["answer"][:4000], values["action"]
            entry.is_active = values["is_active"]
            entry.sort_order = max(0, min(32000, int(values["sort_order"])))
        except (ValidationError, ValueError) as exc:
            messages.error(request, exc.messages[0] if isinstance(exc, ValidationError) else "The order must be a number.")
        else:
            created = entry.pk is None
            entry.save()
            if request.GET.get("q") or request.POST.get("resolve"):
                Unanswered.objects.filter(text=request.POST.get("resolve", request.GET.get("q", ""))).delete()
            audit.record("assistant.knowledge_created" if created else "assistant.knowledge_saved", actor=request.user, target=entry, metadata={"title": entry.title}, request=request)
            messages.success(request, "The entry was saved. The Agent uses it from now on.")
            return redirect("assistant_staff:knowledge")
    return render(request, "assistant/knowledge_form.html", {"entry": entry if pk else None, "v": values, "actions": KnowledgeEntry.ACTIONS, "can_manage": can_manage, "resolve": request.GET.get("q", "")})


@require_POST
@login_required
def entry_delete(request, pk):
    _may(request, manage=True)
    entry = get_object_or_404(KnowledgeEntry, pk=pk)
    audit.record("assistant.knowledge_deleted", actor=request.user, target=entry, metadata={"title": entry.title}, request=request)
    entry.delete()
    messages.success(request, "The entry was deleted.")
    return redirect("assistant_staff:knowledge")


@require_POST
@login_required
def gap_dismiss(request, pk):
    _may(request, manage=True)
    Unanswered.objects.filter(pk=pk).delete()
    return redirect("assistant_staff:knowledge")
