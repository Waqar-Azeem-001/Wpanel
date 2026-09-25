"""
The menu registry (roadmap Section 12.2, Rule 5.3): every menu the portal shows is *data* defined here, never markup.

Each entry names a URL (by URL name), who may see it (a portal permission, or a relationship such as "has a client
account"), which shell it belongs to and where it sits. The three navigation bars, the account and Setup menus, the
active-item highlighting and the breadcrumbs are all generated from this one list, so:

* a link can only exist if its URL name resolves (checked at startup by ``manage.py check`` and again in the tests);
* a link is shown only to someone who may open it (Rule 5.6);
* a page moved or renamed is fixed in one place.

Adding a screen to a menu is adding one ``MenuItem`` here.
"""
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

from apps.accounts.roles import perm

AREAS = ("public", "client", "staff")
POSITIONS = ("main", "right", "account", "setup")
REQUIRES = ("", "anonymous", "client_contact", "no_client_contact", "superuser")


@dataclass(frozen=True)
class MenuItem:
    key: str
    label: object                      # text, or lazy translated text; "{name}" is replaced by the person's name
    url_name: str = ""                 # empty for a group (a menu that only holds other entries)
    areas: tuple = ("client",)         # which shells show it: "public", "client", "staff"
    position: str = "main"             # "main" bar, "right" side, the "account" menu, the "setup" (gear) menu
    parent: str = ""                   # the key of the group it sits in
    permission: str = ""               # a portal permission codename the person must hold
    any_permission: tuple = ()         # or: holding any one of these is enough
    requires: str = ""                 # a relationship: see REQUIRES
    feature_flag: str = ""             # hidden unless settings.FEATURE_FLAGS[flag] is true
    badge: str = ""                    # the name of a counter in BADGES
    icon: str = ""                     # a Bootstrap Icons class, e.g. "bi-bell"
    divider_before: bool = False


def item(key, label, url_name="", **kwargs):
    return MenuItem(key=key, label=label, url_name=url_name, **kwargs)


STAFF = ("staff",)
CLIENT = ("client",)
BOTH = ("client", "staff")

ITEMS = [
    # --- Public ------------------------------------------------------------------------------------------------------
    item("public.plans", _("Plans"), "catalog:product_list", areas=("public",)),
    item("public.domains", _("Domains"), "domains_public:search", areas=("public",)),
    item("public.help", _("Help"), "help:index", areas=("public",)),
    item("public.login", _("Sign in"), "accounts:login", areas=("public",), position="right", requires="anonymous"),
    item("public.register", _("Create account"), "accounts:register", areas=("public",), position="right",
         requires="anonymous"),

    # --- Customer area -----------------------------------------------------------------------------------------------
    item("client.home", _("Home"), "dashboard", areas=CLIENT),
    item("client.services", _("Services"), areas=CLIENT, requires="client_contact"),
    item("client.services.hosting", _("My Hosting"), "hosting_customer:list", areas=CLIENT, parent="client.services"),
    item("client.services.orders", _("My Orders"), "orders_customer:list", areas=CLIENT, parent="client.services"),
    item("client.services.order", _("Order New Services"), "catalog:product_list", areas=CLIENT,
         parent="client.services"),
    item("client.services.addons", _("View Available Addons"), "catalog:addons", areas=CLIENT,
         parent="client.services"),
    item("client.domains", _("Domains"), areas=CLIENT, requires="client_contact"),
    item("client.domains.list", _("My Domains"), "domains_customer:list", areas=CLIENT, parent="client.domains"),
    item("client.domains.register", _("Register a New Domain"), "domains_public:search", areas=CLIENT,
         parent="client.domains"),
    item("client.billing", _("Billing"), areas=CLIENT, requires="client_contact"),
    item("client.billing.invoices", _("My Invoices"), "billing_customer:invoice_list", areas=CLIENT,
         parent="client.billing"),
    item("client.billing.quotes", _("My Quotes"), "billing_customer:quote_list", areas=CLIENT, parent="client.billing"),
    item("client.billing.methods", _("Payment Methods"), "billing_customer:payment_methods", areas=CLIENT,
         parent="client.billing"),
    item("client.support", _("Support"), areas=CLIENT, requires="client_contact"),
    item("client.support.tickets", _("Tickets"), "support_customer:list", areas=CLIENT, parent="client.support"),
    item("client.support.kb", _("Knowledgebase"), "help:index", areas=CLIENT, parent="client.support"),
    item("client.ticket", _("Open Ticket"), "support_customer:new", areas=CLIENT, requires="client_contact"),
    item("client.affiliates", _("Affiliates"), "affiliates_customer:dashboard", areas=CLIENT, requires="client_contact"),
    item("client.plans", _("Plans"), "catalog:product_list", areas=CLIENT, requires="no_client_contact"),
    item("client.help", _("Help"), "help:index", areas=CLIENT, requires="no_client_contact"),
    item("client.cart", _("Cart"), "orders_customer:cart", areas=CLIENT, position="right", requires="client_contact",
         badge="cart", icon="bi-cart"),

    # --- Staff area --------------------------------------------------------------------------------------------------
    item("staff.dashboard", _("Dashboard"), "console:dashboard", areas=STAFF),
    item("staff.clients", _("Clients"), areas=STAFF),
    item("staff.clients.list", _("View / Search Clients"), "clients_staff:list", areas=STAFF, parent="staff.clients",
         permission="view_clients"),
    item("staff.clients.add", _("Add New Client"), "clients_staff:create", areas=STAFF, parent="staff.clients",
         permission="manage_clients"),
    item("staff.clients.services", _("Products / Services"), "hosting_staff:list", areas=STAFF,
         parent="staff.clients", permission="view_hosting"),
    item("staff.clients.domains", _("Domain Registrations"), "domains_staff:list", areas=STAFF,
         parent="staff.clients", permission="view_domains"),
    item("staff.clients.cancellations", _("Cancellation Requests"), "lifecycle_staff:cancellations", areas=STAFF,
         parent="staff.clients", any_permission=("view_hosting", "view_domains")),
    item("staff.clients.lifecycle", _("Service Lifecycle"), "lifecycle_staff:overview", areas=STAFF,
         parent="staff.clients", any_permission=("view_hosting", "view_domains")),
    item("staff.clients.affiliates", _("Manage Affiliates"), "affiliates_staff:overview", areas=STAFF,
         parent="staff.clients", permission="view_affiliates"),
    item("staff.orders", _("Orders"), areas=STAFF),
    item("staff.orders.list", _("List All Orders"), "orders_staff:list", areas=STAFF, parent="staff.orders",
         permission="view_orders"),
    item("staff.orders.add", _("Add New Order"), "orders_staff:new", areas=STAFF, parent="staff.orders",
         permission="manage_orders"),
    item("staff.billing", _("Billing"), areas=STAFF),
    item("staff.billing.overview", _("Billing Overview"), "billing_staff:index", areas=STAFF, parent="staff.billing",
         permission="view_billing"),
    item("staff.billing.transactions", _("Transactions"), "billing_staff:transaction_list", areas=STAFF,
         parent="staff.billing", permission="view_billing"),
    item("staff.billing.invoices", _("Invoices"), "billing_staff:invoice_list", areas=STAFF, parent="staff.billing",
         permission="view_billing"),
    item("staff.billing.items", _("Billable Items"), "billing_staff:billable_list", areas=STAFF,
         parent="staff.billing", permission="view_billing"),
    item("staff.billing.quotes", _("Quotes"), "billing_staff:quote_list", areas=STAFF, parent="staff.billing",
         permission="view_billing"),
    item("staff.billing.renewals", _("Renewals & Upgrades"), "renewals_staff:list", areas=STAFF,
         parent="staff.billing", permission="view_billing"),
    item("staff.support", _("Support"), areas=STAFF),
    item("staff.support.overview", _("Support Overview"), "support_staff:overview", areas=STAFF,
         parent="staff.support", permission="view_support"),
    item("staff.support.tickets", _("Support Tickets"), "support_staff:tickets", areas=STAFF, parent="staff.support",
         permission="view_support"),
    item("staff.support.new", _("Open New Ticket"), "support_staff:ticket_new", areas=STAFF, parent="staff.support",
         permission="view_support"),
    item("staff.support.replies", _("Predefined Replies"), "support_staff:replies", areas=STAFF,
         parent="staff.support", permission="view_support"),
    item("staff.support.kb", _("Knowledgebase"), "support_staff:kb", areas=STAFF, parent="staff.support",
         permission="view_support"),
    item("staff.reports", _("Reports"), "reports_staff:index", areas=STAFF, permission="view_reports"),
    item("staff.utilities", _("Utilities"), areas=STAFF),
    item("staff.utilities.audit", _("Audit Log"), "console:audit_log", areas=STAFF, parent="staff.utilities",
         permission="view_audit_log"),
    item("staff.utilities.email_stats", _("Email Statistics"), "notifications_staff:overview", areas=STAFF,
         parent="staff.utilities", permission="view_settings"),
    item("staff.utilities.email_log", _("Email Message Log"), "notifications_staff:emails", areas=STAFF,
         parent="staff.utilities", permission="view_settings"),

    # --- Setup (the gear menu) ---------------------------------------------------------------------------------------
    item("staff.setup", _("Setup"), areas=STAFF, position="setup", icon="bi-gear"),
    item("staff.setup.products", _("Products / Services"), "catalog_staff:product_list", areas=STAFF,
         position="setup", parent="staff.setup", permission="view_products"),
    item("staff.setup.addons", _("Product Addons"), "catalog_staff:addon_list", areas=STAFF, position="setup",
         parent="staff.setup", permission="view_products"),
    item("staff.setup.tlds", _("Domain Pricing"), "domains_staff:tld_list", areas=STAFF, position="setup",
         parent="staff.setup", permission="view_domains"),
    item("staff.setup.servers", _("Servers"), "catalog_staff:server_list", areas=STAFF, position="setup",
         parent="staff.setup", permission="view_hosting"),
    item("staff.setup.payment_methods", _("Payment Methods"), "billing_staff:payment_methods", areas=STAFF,
         position="setup", parent="staff.setup", permission="view_billing"),
    item("staff.setup.tax", _("Tax Rules"), "billing_staff:tax_rules", areas=STAFF, position="setup",
         parent="staff.setup", permission="view_billing"),
    item("staff.setup.coupons", _("Coupons"), "billing_staff:coupons", areas=STAFF, position="setup",
         parent="staff.setup", permission="view_billing"),
    item("staff.setup.billing", _("Billing Settings"), "billing_staff:settings", areas=STAFF, position="setup",
         parent="staff.setup", permission="view_billing"),
    item("staff.setup.departments", _("Support Departments"), "support_staff:departments", areas=STAFF,
         position="setup", parent="staff.setup", permission="view_support"),
    item("staff.setup.lifecycle", _("Lifecycle Timings"), "lifecycle_staff:settings", areas=STAFF, position="setup",
         parent="staff.setup", permission="view_settings", divider_before=True),
    item("staff.setup.affiliates", _("Affiliate Settings"), "affiliates_staff:settings", areas=STAFF,
         position="setup", parent="staff.setup", permission="view_settings"),
    item("staff.setup.staff", _("Staff & Roles"), "console:staff_users", areas=STAFF, position="setup",
         parent="staff.setup", permission="view_users", divider_before=True),
    item("staff.setup.email", _("Email Provider"), "console:email_providers", areas=STAFF, position="setup",
         parent="staff.setup", permission="view_providers"),
    item("staff.setup.registrar", _("Domain Registrar"), "console:registrars", areas=STAFF, position="setup",
         parent="staff.setup", permission="view_providers"),
    item("staff.setup.brand", _("Brand"), "brand_staff:settings", areas=STAFF, position="setup",
         parent="staff.setup", permission="view_settings"),

    # --- Shared: notifications and the account menu ------------------------------------------------------------------
    item("account.notifications", _("Notifications"), "notifications:inbox", areas=BOTH, position="right",
         badge="unread", icon="bi-bell"),
    item("account.menu", "{name}", areas=BOTH, position="account"),
    item("account.profile", _("Your profile"), "accounts:profile", areas=BOTH, position="account",
         parent="account.menu"),
    item("account.client", _("Your account"), "clients_customer:list", areas=CLIENT, position="account",
         parent="account.menu"),
    item("account.contacts", _("Contacts"), "clients_customer:contacts", areas=CLIENT, position="account",
         parent="account.menu"),
    item("account.cancellations", _("Cancellation requests"), "lifecycle_customer:list", areas=CLIENT,
         position="account", parent="account.menu"),
    item("account.password", _("Change password"), "accounts:password_change", areas=BOTH, position="account",
         parent="account.menu"),
    item("account.emails", _("Email history"), "notifications:emails", areas=CLIENT, position="account",
         parent="account.menu"),
    item("account.preferences", _("Notification preferences"), "notifications:preferences", areas=BOTH,
         position="account", parent="account.menu"),
    item("account.admin", _("Django admin"), "admin:index", areas=BOTH, position="account", parent="account.menu",
         requires="superuser", divider_before=True),
]

BY_KEY = {entry.key: entry for entry in ITEMS}


# --- Counters shown next to an entry ----------------------------------------------------------------------------------

def _unread(request):
    from apps.notifications import services

    return services.unread_count(request.user)


def _cart(request):
    from apps.orders.context_processors import cart_summary

    return cart_summary(request).get("cart_item_count") or 0


BADGES = {"unread": _unread, "cart": _cart}


# --- Building a menu for one request ----------------------------------------------------------------------------------

@dataclass
class Entry:
    key: str
    label: str
    url: str = ""
    active: bool = False
    badge: int = 0
    icon: str = ""
    divider_before: bool = False
    children: list = None

    @property
    def is_group(self):
        return self.children is not None


def _has(user, codename):
    return user.has_perm(perm(codename))


def is_visible(user, entry, *, has_client=None):
    """May this person see (and so open) this entry? Groups are visible when at least one child is (see build)."""
    if entry.feature_flag and not getattr(settings, "FEATURE_FLAGS", {}).get(entry.feature_flag, False):
        return False
    authenticated = bool(user is not None and user.is_authenticated)
    if entry.requires == "anonymous" and authenticated:
        return False
    if entry.requires == "superuser" and not (authenticated and user.is_superuser):
        return False
    if entry.requires in ("client_contact", "no_client_contact"):
        if not authenticated:
            return False
        if has_client is None:
            has_client = user.client_contacts.exists()
        if has_client != (entry.requires == "client_contact"):
            return False
    if entry.permission and not (authenticated and _has(user, entry.permission)):
        return False
    if entry.any_permission and not (authenticated and any(_has(user, code) for code in entry.any_permission)):
        return False
    return True


def _label(entry, user):
    text = str(entry.label)
    if "{name}" in text:
        return text.replace("{name}", (user.get_short_name() if user is not None and user.is_authenticated else ""))
    return text


def build(request, area):
    """{position: [Entry]} for the entries of ``area`` this person may see, with the active ones marked."""
    user = getattr(request, "user", None)
    match = getattr(request, "resolver_match", None)
    current_name = getattr(match, "view_name", "") or ""
    current_namespace = getattr(match, "namespace", "") or ""
    authenticated = bool(user is not None and user.is_authenticated)
    has_client = authenticated and not user.is_staff and user.client_contacts.exists()

    def leaf(entry):
        badge = BADGES[entry.badge](request) if entry.badge and authenticated else 0
        return Entry(key=entry.key, label=_label(entry, user), url=reverse(entry.url_name),
                     active=entry.url_name == current_name, badge=badge, icon=entry.icon,
                     divider_before=entry.divider_before)

    menus = {position: [] for position in POSITIONS}
    for top in (e for e in ITEMS if area in e.areas and not e.parent):
        children = [c for c in ITEMS if c.parent == top.key and area in c.areas]
        if children or not top.url_name:
            shown = [leaf(c) for c in children if is_visible(user, c, has_client=has_client)]
            if not shown or not is_visible(user, top, has_client=has_client):
                continue
            group_ns = {c.url_name.split(":")[0] for c in children if ":" in c.url_name}
            group = Entry(key=top.key, label=_label(top, user), icon=top.icon, children=shown,
                          active=any(e.active for e in shown) or (current_namespace in group_ns and bool(current_namespace)))
            menus[top.position].append(group)
        elif is_visible(user, top, has_client=has_client):
            menus[top.position].append(leaf(top))
    return menus


def area_of(request):
    from apps.core.context_processors import area_for

    return area_for(getattr(request, "user", None))


# --- Breadcrumbs from the same registry -------------------------------------------------------------------------------

def breadcrumbs_for(request):
    """[(label, url)] for a page that is a registry entry: Home > group > page. Pages that are not entries return []."""
    match = getattr(request, "resolver_match", None)
    name = getattr(match, "view_name", "") or ""
    if not name:
        return []
    area = area_of(request)
    entry = next((e for e in ITEMS if e.url_name == name and area in e.areas and e.position != "right"), None)
    if entry is None or entry.key in ("client.home", "account.profile", "public.login", "public.register"):
        return []
    trail = []
    parent = BY_KEY.get(entry.parent) if entry.parent else None
    if parent is not None:
        trail.append((str(parent.label) if "{name}" not in str(parent.label) else "", ""))
    trail.append((str(entry.label), reverse(entry.url_name)))
    return [(_("Home"), reverse("home"))] + [t for t in trail if t[0]]


# --- Validation (used by the startup check and the tests) -------------------------------------------------------------

def problems():
    """Every fault in the registry as a short sentence: an empty list means every entry is sound."""
    from apps.accounts.roles import ALL_CODENAMES

    found, seen = [], set()
    for entry in ITEMS:
        if entry.key in seen:
            found.append(f"{entry.key}: the key is used twice")
        seen.add(entry.key)
        if not str(entry.label).strip():
            found.append(f"{entry.key}: no label")
        if entry.position not in POSITIONS:
            found.append(f"{entry.key}: unknown position {entry.position!r}")
        if entry.requires not in REQUIRES:
            found.append(f"{entry.key}: unknown requirement {entry.requires!r}")
        if entry.badge and entry.badge not in BADGES:
            found.append(f"{entry.key}: unknown badge {entry.badge!r}")
        if not entry.areas or any(a not in AREAS for a in entry.areas):
            found.append(f"{entry.key}: unknown area in {entry.areas!r}")
        for codename in ((entry.permission,) if entry.permission else ()) + tuple(entry.any_permission):
            if codename not in ALL_CODENAMES:
                found.append(f"{entry.key}: unknown permission {codename!r}")
        if entry.parent:
            parent = BY_KEY.get(entry.parent)
            if parent is None:
                found.append(f"{entry.key}: the parent {entry.parent!r} does not exist")
            elif parent.url_name:
                found.append(f"{entry.key}: the parent {entry.parent!r} is a link, not a group")
            elif not set(entry.areas) <= set(parent.areas):
                found.append(f"{entry.key}: shown in an area its group is not")
        if entry.url_name:
            try:
                path = reverse(entry.url_name)
            except NoReverseMatch:
                found.append(f"{entry.key}: the URL name {entry.url_name!r} does not resolve")
            else:
                if urlsplit(path).netloc:
                    found.append(f"{entry.key}: {entry.url_name!r} is not an internal path")
        elif not any(c.parent == entry.key for c in ITEMS):
            found.append(f"{entry.key}: a group with nothing in it")
    return found
