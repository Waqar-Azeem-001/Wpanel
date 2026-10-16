"""How the AI Agent is doing: what customers ask, how much it solves alone, how long people wait for the team, what customers approve, whether answers helped."""
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from .models import Conversation, KnowledgeEntry, Message, PendingAction, SenderType, Unanswered


def report(days=7, now=None):
    now = now or timezone.now()
    since = now - timedelta(days=days)
    chats = Conversation.objects.filter(created_at__gte=since)
    total = chats.count()
    with_person = chats.filter(Q(handoff_requested_at__isnull=False) | Q(ticket__isnull=False)).count()
    waits = []
    for c in chats.filter(handoff_requested_at__isnull=False).order_by("-id")[:300]:
        joined = c.messages.filter(sender=SenderType.EVENT, tool_call__kind="joined").order_by("id").first()
        if joined:
            waits.append((joined.created_at - c.handoff_requested_at).total_seconds())
    messages = Message.objects.filter(conversation__in=chats)
    asked = messages.filter(sender=SenderType.CLIENT).count()
    answers = messages.filter(sender=SenderType.AI)
    up, down = answers.filter(feedback=1).count(), answers.filter(feedback=-1).count()
    changes = PendingAction.objects.filter(created_at__gte=since)
    per_day = []
    for offset in range(min(days, 14) - 1, -1, -1):
        day = (now - timedelta(days=offset)).date()
        per_day.append((day, Conversation.objects.filter(created_at__date=day).count()))
    tools = list(messages.filter(sender=SenderType.EVENT, tool_call__kind__isnull=True).exclude(body="").values("body").annotate(n=Count("id")).order_by("-n")[:6])
    return {
        "days": days, "chats": total, "with_person": with_person, "alone": total - with_person, "alone_percent": round(100 * (total - with_person) / total) if total else 0,
        "tickets": chats.filter(ticket__isnull=False).count(), "asked": asked, "answered": answers.count(), "average_wait": round(sum(waits) / len(waits)) if waits else None, "waits": len(waits),
        "up": up, "down": down, "helpful_percent": round(100 * up / (up + down)) if up + down else None,
        "changes": {"proposed": changes.count(), "done": changes.filter(status="done").count(), "failed": changes.filter(status="failed").count(), "cancelled": changes.filter(status="cancelled").count(),
                    "expired": changes.filter(status="expired").count(), "locked": changes.filter(status="locked").count(), "with_code": changes.filter(needs="code").count()},
        "per_day": per_day, "per_day_max": max([n for _d, n in per_day] + [1]), "tools": tools,
        "top_entries": list(KnowledgeEntry.objects.filter(hits__gt=0).order_by("-hits")[:8]), "gaps": list(Unanswered.objects.all()[:8]), "gap_total": Unanswered.objects.count(),
        "entries": KnowledgeEntry.objects.filter(is_active=True).count()}
