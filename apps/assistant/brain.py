"""
The Agent's own brain, with no outside service: it matches what the customer wrote against the knowledge entries staff keep in Setup, runs the live check an entry asks for on the
customer's own account (through the same tools as the Claude mode), and writes the answer. What it cannot match is kept (Unanswered) so staff can teach it.
"""
import re

from django.db.models import F

from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from . import live, tools
from .models import KnowledgeEntry, Message, SenderType, Unanswered

DOMAIN = re.compile(r"\b((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})\b")
STOP = {"the", "a", "an", "is", "are", "my", "me", "i", "to", "of", "in", "on", "it", "and", "or", "for", "please", "plz", "can", "you", "do", "how", "what", "why", "hai", "ho", "ka", "ki", "ke", "mera",
        "meri", "mere", "se", "ko", "mein", "main", "kya", "kaise", "kar", "karo", "karna", "nahi", "ye", "yeh", "wo", "woh", "aur", "par", "pe", "with", "this", "that", "be", "get"}
GREETING = {"hi", "hello", "hey", "helo", "salam", "salaam", "assalam", "assalamualaikum", "aoa", "hii", "hy", "good", "morning", "evening", "afternoon"}
THANKS = {"thanks", "thank", "thankyou", "shukriya", "shukria", "jazakallah", "thx", "ok", "okay", "theek", "acha", "achha"}
PERSON = ("real person", "human", "live agent", "live chat", "talk to someone", "talk to a person", "speak to someone", "support team", "insaan", "insan", "kisi se baat", "banda", "bande se", "staff se", "manager")
MIN_SCORE = 4


def normalise(text):
    return " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.\- ]+", " ", (text or "").lower())).strip() + " "


def words(text):
    return [w.strip(".-") for w in normalise(text).split() if w.strip(".-")]


def score(entry, text, tokens):
    best, extra = 0, 0
    for line in entry.keywords.splitlines():
        phrase = normalise(line)
        phrase_words = [w for w in words(line) if w]
        if not phrase_words:
            continue
        if phrase in text:
            value = 4 + len(phrase_words)
        else:
            core = {w for w in phrase_words if w not in STOP}
            if len(core) >= 2 and core <= tokens:
                value = 3 + len(core)
            elif len(core) == 1 and len(phrase_words) == 1 and core <= tokens and len(next(iter(core))) >= 4:
                value = 4
            else:
                continue
        if value > best:
            extra += 0.5 if best else 0
            best = value
        else:
            extra += 0.5
    return best + min(extra, 1.5) if best else 0


def best_entry(text):
    normal = normalise(text)
    tokens = {w for w in words(text)}
    ranked = []
    for entry in KnowledgeEntry.objects.filter(is_active=True):
        value = score(entry, normal, tokens)
        if value >= MIN_SCORE:
            ranked.append((value, -entry.sort_order, entry))
    ranked.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return ranked[0][2] if ranked else None


def domain_in(text):
    for found in DOMAIN.findall((text or "").lower()):
        if "." in found and not found.endswith((".png", ".jpg", ".php", ".html")):
            return found
    return ""


# --- turning a tool's answer into words ------------------------------------------------------------------------------------------------------

def _num(value):
    return "-" if value is None else str(value)


def format_result(action, out):
    if "error" in out:
        return "I could not check that: " + out["error"]
    if action == "website_status":
        state = "opens" if out.get("reachable") else "does not open"
        line = f"{out['domain']} {state}" + (f" (HTTP {out['http_status']})" if out.get("http_status") else "") + f". First byte {out.get('first_byte_ms', 0)} ms, full page {out.get('total_ms', 0)} ms, speed score {out.get('score', 0)}/100."
        if out.get("problem"):
            line += f" Problem: {out['problem']}"
        if out.get("advice"):
            line += "\n- " + "\n- ".join(out["advice"][:4])
        return line
    if action == "ssl":
        if not out.get("active"):
            return f"{out.get('domain', 'The domain')} has no active SSL certificate right now."
        left = out.get("days_left")
        return f"{out.get('domain')}: SSL is active (issuer {out.get('issuer') or 'unknown'})" + (f", {left} days left" if left is not None else "") + f"; auto-renew {'on' if out.get('auto_renew') else 'off'}, HTTPS redirect {'on' if out.get('https_redirect') else 'off'}."
    if action == "disk":
        text = f"Disk: {_num(out.get('disk_used_mb'))} MB of {_num(out.get('disk_limit_mb'))} MB"
        if out.get("disk_percent") is not None:
            text += f" ({out['disk_percent']}%)"
        return text + f". Bandwidth this month: {_num(out.get('bandwidth_used_mb'))} MB of {_num(out.get('bandwidth_limit_mb'))} MB."
    if action == "error_log":
        lines = out.get("last_lines") or []
        return "The error log is empty: nothing is being reported." if not lines else "Newest error log lines:\n" + "\n".join(lines[-6:])
    if action == "dns":
        rows = out.get("records") or []
        return f"DNS records of {out.get('domain')}:\n" + "\n".join(f"{r.get('type')}  {r.get('name')}  {r.get('value')}" for r in rows[:14]) if rows else "There are no DNS records."
    if action == "email_quota":
        rows = out.get("mailboxes") or []
        if not rows:
            return "You have no mailboxes."
        return "\n".join(f"{b['address']}: {_num(b.get('used_mb'))} MB of {_num(b.get('quota_mb') or 'unlimited')} MB" + (f" ({b['percent_used']}%)" if b.get("percent_used") is not None else "") for b in rows[:12])
    if action == "invoices":
        rows = out.get("invoices") or []
        return "You have no invoices yet." if not rows else "\n".join(f"{i['number'] or 'Draft'}: {i['status']}, {i['total']} {i['currency']}" + (f", due {i['due_date']}" if i.get("due_date") else "") for i in rows[:8])
    if action == "services":
        rows = out.get("hosting_plans") or []
        if not rows:
            return "You have no active hosting plan yet."
        return "\n".join(f"{p['domain']}: {p['plan'] or 'hosting'} ({p['status']})" + ("; websites: " + ", ".join(w["domain"] for w in p["websites"]) if p.get("websites") else "") for p in rows)
    return ""


TOOL = {"website_status": "check_website_status", "ssl": "check_ssl", "disk": "check_disk_usage", "error_log": "get_error_log", "dns": "get_dns_records", "email_quota": "check_email_quota",
        "invoices": "get_invoices", "services": "get_services"}
NEEDS_DOMAIN = {"website_status", "ssl", "disk", "error_log", "dns", "email_quota"}


def _say(conversation, text):
    return Message.objects.create(conversation=conversation, sender=SenderType.AI, body=text[:6000])


def _run(ctx, action, domain, saved):
    name = TOOL[action]
    args = {"domain": domain} if action in NEEDS_DOMAIN else {}
    out = tools.run(ctx, name, args)
    audit.record("assistant.tool", actor=ctx.user, target=ctx.conversation, metadata={"tool": name, "domain": domain[:100], "failed": "error" in out}, request=ctx.request)
    saved.append(Message.objects.create(conversation=ctx.conversation, sender=SenderType.EVENT, body=name, tool_call={"tool": name, "domain": domain[:100], "failed": "error" in out}))
    return format_result(action, out)


def _remember_gap(text):
    text = " ".join((text or "").split())[:300]
    if len(text) < 4:
        return
    row, made = Unanswered.objects.get_or_create(text=text)
    if not made:
        Unanswered.objects.filter(pk=row.pk).update(count=F("count") + 1)


def _owned_domains(user):
    names = []
    for account in tools.accounts(user):
        names.append(account.domain.lower())
    return names


def reply(user, conversation, text, request=None):
    """Answer one customer message. Returns the new messages (tool events, then the answer)."""
    ctx = tools.Context(user, conversation, request)
    saved, clean = [], " ".join((text or "").split())
    tokens = set(words(clean))
    normal = normalise(clean)
    domain = domain_in(clean)

    def finish(answer, pending=None):
        conversation.pending = pending or {}
        conversation.save(update_fields=["pending", "updated_at"])
        saved.append(_say(conversation, answer))
        return saved

    # the customer is answering "which domain?"
    pending = conversation.pending or {}
    if pending.get("action") in TOOL and (domain or tokens & {d.split(".")[0] for d in _owned_domains(user)}):
        picked = domain or next((d for d in _owned_domains(user) if d.split(".")[0] in tokens), "")
        return finish(_run(ctx, pending["action"], picked, saved))

    if any(p in normal for p in PERSON):
        out = live.ask_person(user, conversation, "The customer asked for a person: " + clean[:150], request=request)
        return finish("Of course. I asked someone from our team to join this chat; please keep this window open." if out["status"] == "waiting" else "A person is already on this chat.")
    if tokens and tokens <= GREETING | {"there", "team", "sir", "madam"}:
        return finish("Hello! I am your AI Agent. Ask me about your website, email, SSL, domains, invoices, or how to do something in the panel. For example: Is my SSL working? Why is my website slow? How do I create an email?")
    if tokens and tokens <= THANKS | {"you", "so", "much", "a", "lot", "bohat", "bahut"}:
        return finish("You are welcome! Ask me anything else, or press Talk to a person if you would like someone from our team.")

    entry = best_entry(clean)
    if entry is None:
        _remember_gap(clean)
        from apps.support import kb

        found = []
        for word in [w for w in words(clean) if w not in STOP and len(w) > 3][:4]:
            for article in kb.search_public(word)[:3]:
                if article not in found:
                    found.append(article)
        if found:
            lines = "\n".join(f"- {a.title}: /help/{a.category.slug}/{a.slug}/" for a in found[:4])
            return finish("I am not sure I understood, but these help articles may answer it:\n" + lines + "\n\nIf not, tell me more about the problem, or press Talk to a person.")
        return finish("I am not sure I understood that. Could you say it another way, for example: my website is slow, SSL problem, create an email, my invoices? Or press Talk to a person and someone from our team will help.")

    KnowledgeEntry.objects.filter(pk=entry.pk).update(hits=F("hits") + 1)
    answer = entry.answer.strip()
    action = entry.action
    if action == "handoff":
        out = live.ask_person(user, conversation, entry.title + ": " + clean[:150], request=request)
        return finish(answer if out["status"] == "waiting" else "A person is already on this chat.")
    if action == "ticket":
        try:
            ticket = tools._open(ctx, "Help from the AI Agent chat", "The customer asked for a ticket in the AI Agent chat.\n\n" + tools.transcript(conversation, limit=12))
        except ServiceError as exc:
            return finish(f"I could not open a ticket: {exc.message}")
        return finish(f"{answer}\nTicket {ticket.reference}.")
    if action in TOOL:
        owned = _owned_domains(user)
        if action in NEEDS_DOMAIN and not domain:
            if len(owned) == 1:
                domain = owned[0]
            elif not owned:
                return finish(answer + "\n\nI cannot see an active hosting plan on your account yet.")
            else:
                return finish(answer + "\n\nWhich domain do you mean? " + ", ".join(owned), pending={"action": action})
        result = _run(ctx, action, domain, saved)
        return finish(answer + ("\n\n" + result if result else ""))
    return finish(answer)
