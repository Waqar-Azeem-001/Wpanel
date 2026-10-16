"""
The first help articles, written from what the customer panel does today. Staff change, add and remove them in Support > Knowledge base.
The articles live in apps/support/kb_content/, one file per topic. A link to another article is written [[Exact title of the article]].
(slug, name, icon, description) for the categories; (category slug, title, summary, featured, body) for the articles.
"""
from .kb_content import (account_support, billing_orders, domains_dns, email, extra_email_hosting, extra_getting_started, extra_rest, extra_websites_domains, getting_started, hosting_tools,
                         security_backups, troubleshooting, websites_wordpress)

CATEGORIES = [
    ("getting-started", "Getting started", "bi-rocket-takeoff", "Your account, your first website and a tour of the panel."),
    ("websites-wordpress", "Websites and WordPress", "bi-wordpress", "Create, copy, test and look after your websites."),
    ("domains-dns", "Domains and DNS", "bi-globe2", "Register, transfer, point and manage your domain names."),
    ("email", "Email", "bi-envelope", "Mailboxes, webmail, forwarding and a full inbox."),
    ("hosting-tools", "Hosting tools", "bi-hdd-network", "Files, databases, PHP, cron jobs, logs and more."),
    ("security-backups", "Security and backups", "bi-shield-check", "SSL, backups, malware scans and protected folders."),
    ("troubleshooting", "Troubleshooting", "bi-tools", "Fix common website, email and domain errors."),
    ("billing-orders", "Billing and orders", "bi-receipt", "Invoices, payments, renewals and orders."),
    ("account-support", "Account and support", "bi-headset", "Tickets, the AI Agent and keeping your account safe."),
]

_BASE = (getting_started.ARTICLES + websites_wordpress.ARTICLES + domains_dns.ARTICLES + email.ARTICLES + hosting_tools.ARTICLES + security_backups.ARTICLES
         + troubleshooting.ARTICLES + billing_orders.ARTICLES + account_support.ARTICLES)
EXTRAS = {**extra_getting_started.EXTRAS, **extra_websites_domains.EXTRAS, **extra_email_hosting.EXTRAS, **extra_rest.EXTRAS}
FAQ = "## Frequently asked questions"


def _with_extra(body, extra):
    """The longer sections go in front of the questions, so the questions stay last."""
    if not extra:
        return body
    if FAQ in body:
        head, tail = body.split(FAQ, 1)
        return head.rstrip() + "\n\n" + extra.strip() + "\n\n" + FAQ + tail
    return body.rstrip() + "\n\n" + extra.strip() + "\n"


ARTICLES = [(cat, title, summary, featured, _with_extra(body, EXTRAS.get(title, ""))) for cat, title, summary, featured, body in _BASE]
