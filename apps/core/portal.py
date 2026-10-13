"""
Sidebar panels and status filters for the customer area (roadmap Section 10.5).

A panel is a titled list of links. Every link is built from a URL name (Rule 5.1); "active" means "you are here";
counts come from the same queryset the page lists, so a number on a link is the number of rows behind it.
"""
from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.urls import reverse


@dataclass
class Link:
    label: str
    url: str
    active: bool = False
    count: int | None = None
    icon: str = ""
    post: bool = False  # rendered as a small POST form (sign out)
    group: str = ""  # a heading shown above the first link of each group (hosting workspace menu)
    disabled: bool = False  # shown muted with a "Soon" tag and no address: nothing to open yet


class WorkspaceMenu(list):
    """The panels of one website's workspace. The layout reads ``.workspace``: on a phone this menu is opened from a button in the page header instead of sitting under the page."""

    workspace = True


@dataclass
class Panel:
    title: str
    links: list = field(default_factory=list)
    lines: list = field(default_factory=list)  # plain text lines shown above the links (name, company, address)


def link(request, label, url_name, *args, query=None, count=None, icon="", active=None):
    """A link to a named URL. Active when this is the current page (and, with ``query``, the same filter)."""
    url = reverse(url_name, args=args)
    if query:
        url += "?" + urlencode(query)
    if active is None:
        active = request.path == reverse(url_name, args=args) and not query
    return Link(label, url, active=active, count=count, icon=icon)


def status_filter(request, queryset, choices, *, url_name, param="status", all_label="All", apply=None, default="",
                  current=None):
    """
    Filter ``queryset`` by ``?status=`` and build the "View" panel that goes with it.

    Returns ``(filtered_queryset, panel)``. ``choices`` is a list of (value, label); ``apply(queryset, value)`` narrows the
    queryset (default: ``status=value``; an "overdue" invoice is derived, not stored). Each link's count is the number of
    rows the list would show for it. A value that is not a choice is ignored, never an error. ``all_label=None`` leaves out
    the "All" link (when a ``default`` filter applies and one of the choices is already "all"); ``current`` overrides what
    the address says (used to honour an older query name).
    """
    apply = apply or (lambda rows, value: rows.filter(status=value))
    values = [value for value, _label in choices]
    if current is None:
        current = request.GET.get(param, default)
    if current not in values:
        current = default if default in values else ""
    links = []
    if all_label:
        links.append(link(request, all_label, url_name, count=queryset.count(), active=current == ""))
    for value, label in choices:
        links.append(link(request, label, url_name, query={param: value}, count=apply(queryset, value).count(),
                          active=current == value))
    return (apply(queryset, current) if current else queryset), Panel("View", links)


# --- Panels shared by several customer pages ---------------------------------------------------------------------------

def your_info(client):
    """The account holder's details, for the dashboard sidebar."""
    lines = [client.display_name]
    if client.company_name and client.company_name != client.display_name:
        lines.append(client.company_name)
    lines += [line for line in (client.email, client.phone) if line]
    address = ", ".join(part for part in (client.address_line1, client.city, client.country) if part)
    if address:
        lines.append(address)
    return Panel("Your info", lines=lines)


def contacts_panel(request, client):
    """The people on the account, each with their role."""
    panel = Panel("Contacts")
    for contact in client.contacts.select_related("user").order_by("role", "id"):
        panel.lines.append(f"{contact.user.get_full_name() or contact.user.email} ({contact.get_role_display()})")
    panel.links.append(link(request, "All contacts", "clients_customer:contacts"))
    return panel


def shortcuts(request):
    return Panel("Shortcuts", [
        link(request, "Order new services", "catalog:product_list", icon="bi-box"),
        link(request, "Register a domain", "domains_public:search", icon="bi-globe"),
        link(request, "Open a ticket", "support_customer:new", icon="bi-life-preserver"),
    ])


def actions_panel(request, title, entries):
    """A panel of plain links: ``entries`` is a list of (label, url name, icon)."""
    return Panel(title, [link(request, label, name, icon=icon, active=False) for label, name, icon in entries])


def can_cancel(user, service):
    """Whether to offer "request cancellation": exactly when the service card does (owner, service still live) or a
    request is already open (then the link goes to it)."""
    from apps.lifecycle import services as lifecycle

    return lifecycle.open_request_for(service) is not None or (
        lifecycle.can_request(user, service) and lifecycle.is_live(service))


def service_sidebar(request, account):
    # The website workspace menu (Hostinger-style): the website's own overview first, then its tools in groups, then everything about the plan.
    plan = [link(request, "Information", "hosting_customer:information", account.pk, icon="bi-info-circle"),
            link(request, "Add-ons", "hosting_customer:addons", account.pk, icon="bi-plus-square")]
    if account.status == "active":
        plan.append(link(request, "Upgrade / downgrade", "renewals_customer:hosting_upgrade", account.pk, icon="bi-arrow-up-circle", active=False))
    if can_cancel(request.user, account):
        plan.append(link(request, "Request cancellation", "lifecycle_customer:new", "hosting", account.pk, icon="bi-x-circle", active=False))
    plan.append(link(request, "Open a ticket", "support_customer:new", icon="bi-life-preserver", active=False))
    from apps.hosting import dashboard  # the hosting workspace menu lives with hosting; imported here to keep core light

    overview = link(request, "Overview", "hosting_customer:detail", account.pk, icon="bi-speedometer2")
    return WorkspaceMenu([Panel("Website", [overview, *dashboard.workspace_links(request, account)]), Panel("Plan", plan)])


def domain_sidebar(request, domain):
    # The same workspace shape as a website: the domain's own pages in one menu (a drawer on a phone), then what concerns the registration itself.
    pk = domain.pk
    tools = [link(request, "Overview", "domains_customer:detail", pk, icon="bi-speedometer2"),
             link(request, "DNS records", "domains_customer:dns_add", pk, icon="bi-diagram-3"),
             link(request, "Nameservers", "domains_customer:nameservers", pk, icon="bi-hdd-network"),
             link(request, "Auto renew", "domains_customer:auto_renew", pk, icon="bi-arrow-repeat"),
             link(request, "Registrar lock", "domains_customer:lock_tab", pk, icon="bi-lock")]
    registration = []
    if domain.status == "active":
        registration.append(link(request, "Renew", "domains_customer:renew_tab", pk, icon="bi-calendar-plus"))
    if can_cancel(request.user, domain):
        registration.append(link(request, "Request cancellation", "lifecycle_customer:new", "domain", pk, icon="bi-x-circle", active=False))
    registration.append(link(request, "Open a ticket", "support_customer:new", icon="bi-life-preserver", active=False))
    return WorkspaceMenu([Panel("Domain", tools), Panel("Registration", registration)])
