"""
The records that belong to a client, for the staff profile page. Each section is
included only if the viewer holds that module's view permission, so the profile
never shows more than the viewer could see in the module itself. Other apps are
imported lazily: they depend on ``clients``, not the other way round.
"""
from apps.accounts.roles import perm

LIMIT = 8


def account_records(user, client):
    """A list of ``{"title", "rows", "total", "list_url"}`` sections (rows are short dicts for the template)."""
    from django.urls import reverse

    sections = []

    if user.has_perm(perm("view_orders")):
        rows = client.orders.order_by("-created_at", "-id")
        sections.append({"title": "Orders", "total": rows.count(), "list_url": reverse("orders_staff:list"),
                         "rows": [{"label": o.reference, "url": reverse("orders_staff:detail", args=[o.pk]),
                                   "status": o.get_status_display(), "status_key": o.status,
                                   "amount": f"{o.currency} {o.total}", "date": o.created_at} for o in rows[:LIMIT]]})

    if user.has_perm(perm("view_billing")):
        rows = client.invoices.order_by("-created_at", "-id")
        sections.append({"title": "Invoices", "total": rows.count(),
                         "list_url": reverse("billing_staff:invoice_list") + f"?q={client.email}",
                         "rows": [{"label": i.reference, "url": reverse("billing_staff:invoice_detail", args=[i.pk]),
                                   "status": i.display_status_label, "status_key": i.display_status,
                                   "amount": f"{i.currency} {i.total}", "date": i.created_at}
                                  for i in rows[:LIMIT]]})
        rows = client.transactions.select_related("invoice").order_by("-occurred_at", "-id")
        sections.append({"title": "Payments", "total": rows.count(), "list_url": reverse("billing_staff:transaction_list"),
                         "rows": [{"label": f"{t.get_type_display()} on {t.invoice.reference}",
                                   "url": reverse("billing_staff:invoice_detail", args=[t.invoice_id]),
                                   "status": t.get_status_display(), "status_key": t.status,
                                   "amount": f"{t.currency} {t.amount}", "date": t.occurred_at}
                                  for t in rows[:LIMIT]]})
        rows = client.quotes.order_by("-created_at", "-id")
        sections.append({"title": "Quotes", "total": rows.count(), "list_url": reverse("billing_staff:quote_list"),
                         "rows": [{"label": q.reference, "url": reverse("billing_staff:quote_detail", args=[q.pk]),
                                   "status": q.display_status_label, "status_key": q.status,
                                   "amount": f"{q.currency} {q.total}", "date": q.created_at} for q in rows[:LIMIT]]})

    if user.has_perm(perm("view_domains")):
        rows = client.domains.order_by("name")
        sections.append({"title": "Domains", "total": rows.count(), "list_url": reverse("domains_staff:list"),
                         "rows": [{"label": d.name, "url": reverse("domains_staff:detail", args=[d.pk]),
                                   "status": d.get_status_display(), "status_key": d.status, "amount": "",
                                   "date": d.created_at} for d in rows[:LIMIT]]})

    if user.has_perm(perm("view_hosting")):
        rows = client.hosting_accounts.order_by("domain")
        sections.append({"title": "Hosting", "total": rows.count(), "list_url": reverse("hosting_staff:list"),
                         "rows": [{"label": h.domain, "url": reverse("hosting_staff:detail", args=[h.pk]),
                                   "status": h.get_status_display(), "status_key": h.status, "amount": "",
                                   "date": h.created_at} for h in rows[:LIMIT]]})

    if user.has_perm(perm("view_support")):
        rows = client.tickets.order_by("-last_activity_at", "-id")
        sections.append({"title": "Tickets", "total": rows.count(),
                         "list_url": reverse("support_staff:tickets") + f"?q={client.email}",
                         "rows": [{"label": f"{t.reference} {t.subject[:40]}",
                                   "url": reverse("support_staff:ticket", args=[t.pk]),
                                   "status": t.get_status_display(), "status_key": t.status, "amount": "",
                                   "date": t.last_activity_at} for t in rows[:LIMIT]]})

    if user.has_perm(perm("view_hosting")) or user.has_perm(perm("view_domains")):
        from apps.lifecycle import services as lifecycle

        rows = lifecycle.visible_requests_for_user(user).filter(client=client)
        sections.append({"title": "Cancellations", "total": rows.count(),
                         "list_url": reverse("lifecycle_staff:cancellations") + f"?status=all&q={client.email}",
                         "rows": [{"label": f"{c.code} {c.service_name}",
                                   "url": reverse("lifecycle_staff:cancellation", args=[c.pk]),
                                   "status": c.get_status_display(), "status_key": c.status, "amount": "",
                                   "date": c.created_at} for c in rows[:LIMIT]]})

    if user.has_perm(perm("view_affiliates")):
        from apps.affiliates.models import Referral

        referral = Referral.objects.select_related("affiliate").filter(client=client).first()
        rows = []
        if referral is not None:
            rows.append({"label": f"Referred by {referral.affiliate.code}",
                         "url": reverse("affiliates_staff:affiliate", args=[referral.affiliate_id]),
                         "status": referral.get_source_display(), "status_key": "sent", "amount": "",
                         "date": referral.created_at})
            for c in referral.commissions.select_related("invoice").order_by("-created_at")[:LIMIT]:
                rows.append({"label": f"Commission {c.code}", "url": reverse("affiliates_staff:commissions") +
                             f"?q={c.invoice.number}", "status": c.get_status_display(), "status_key": c.status,
                             "amount": f"{c.currency} {c.amount}", "date": c.created_at})
        sections.append({"title": "Affiliate", "total": len(rows), "list_url": reverse("affiliates_staff:overview"),
                         "rows": rows})

    return sections
