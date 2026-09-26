"""
The menu registry (roadmap Section 12.2, Rule 5.3): every menu the portal shows is *data* defined here, never markup.

Each entry names a URL (by URL name), who may see it (a portal permission, or a relationship such as "has a client
account"), which shell it belongs to and where it sits. The store's top bar, the left rail of the signed-in areas (with
its section headings), the top bar's bell and profile menu, the phone's bottom bar, the active-item highlighting, the
"jump to" list and the breadcrumbs are all generated from this one list, so:

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
POSITIONS = ("main", "right", "account")
REQUIRES = ("", "anonymous", "client_contact", "no_client_contact", "superuser")


@dataclass(frozen=True)
class MenuItem:
    key: str
    label: object                      # text, or lazy translated text; "{name}" is replaced by the person's name
    url_name: str = ""                 # empty for a group (a menu that only holds other entries)
    areas: tuple = ("client",)         # which shells show it: "public", "client", "staff"
    position: str = "main"             # "main" = the left rail, "right" = the top bar, "account" = the profile menu
    section: object = ""               # the heading a top-level rail entry sits under (staff rail)
    mobile_tab: bool = False           # also a tab in the phone bottom bar (customer area)
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
    # --- Public (the storefront keeps its top bar) -------------------------------------------------------------------
    item("public.plans", _("Plans"), "catalog:product_list", areas=("public",)),
    item("public.domains", _("Domains"), "domains_public:search", areas=("public",)),
    item("public.help", _("Help"), "help:index", areas=("public",)),
    item("public.cart", _("Cart"), "orders_customer:cart", areas=("public",), position="right", badge="cart", icon="bi-cart",
         requires="anonymous"),
    item("public.login", _("Sign in"), "accounts:login", areas=("public",), position="right", requires="anonymous"),
    item("public.register", _("Create account"), "accounts:register", areas=("public",), position="right",
         requires="anonymous"),

    # --- Customer area: the left rail; five of its entries are also the phone's bottom bar -------------------------
    item("client.home", _("Overview"), "dashboard", areas=CLIENT, icon="bi-house-door", mobile_tab=True),
    item("client.services", _("Services"), areas=CLIENT, requires="client_contact", icon="bi-hdd-rack", mobile_tab=True),
    item("client.services.hosting", _("My hosting"), "hosting_customer:list", areas=CLIENT, parent="client.services"),
    item("client.services.order", _("Order new services"), "catalog:product_list", areas=CLIENT,
         parent="client.services"),
    item("client.services.addons", _("Add-ons"), "catalog:addons", areas=CLIENT, parent="client.services"),
    item("client.domains", _("Domains"), areas=CLIENT, requires="client_contact", icon="bi-globe2", mobile_tab=True),
    item("client.domains.list", _("My domains"), "domains_customer:list", areas=CLIENT, parent="client.domains"),
    item("client.domains.register", _("Register a domain"), "domains_public:search", areas=CLIENT,
         parent="client.domains"),
    item("client.orders", _("Orders"), "orders_customer:list", areas=CLIENT, requires="client_contact", icon="bi-bag"),
    item("client.billing", _("Billing"), areas=CLIENT, requires="client_contact", icon="bi-credit-card-2-front",
         mobile_tab=True),
    item("client.billing.invoices", _("Invoices"), "billing_customer:invoice_list", areas=CLIENT,
         parent="client.billing"),
    item("client.billing.quotes", _("Quotes"), "billing_customer:quote_list", areas=CLIENT, parent="client.billing"),
    item("client.billing.methods", _("Payment methods"), "billing_customer:payment_methods", areas=CLIENT,
         parent="client.billing"),
    item("client.support", _("Support"), areas=CLIENT, requires="client_contact", icon="bi-life-preserver",
         mobile_tab=True),
    item("client.support.tickets", _("Tickets"), "support_customer:list", areas=CLIENT, parent="client.support"),
    item("client.support.new", _("Open a ticket"), "support_customer:new", areas=CLIENT, parent="client.support"),
    item("client.support.kb", _("Help articles"), "help:index", areas=CLIENT, parent="client.support"),
    item("client.affiliates", _("Affiliates"), "affiliates_customer:dashboard", areas=CLIENT, requires="client_contact",
         icon="bi-share"),
    item("client.plans", _("Plans"), "catalog:product_list", areas=CLIENT, requires="no_client_contact",
         icon="bi-box-seam"),
    item("client.help", _("Help"), "help:index", areas=CLIENT, requires="no_client_contact", icon="bi-question-circle"),
    item("client.cart", _("Cart"), "orders_customer:cart", areas=CLIENT, position="right", requires="client_contact",
         badge="cart", icon="bi-cart"),

    # --- Staff area: the same rail, in sections. What a person may not open is not listed (Rule 5.6). -----------------
    item("staff.dashboard", _("Overview"), "console:dashboard", areas=STAFF, icon="bi-grid-1x2"),

    item("staff.clients", _("Clients"), areas=STAFF, section=_("People"), icon="bi-people"),
    item("staff.clients.list", _("All clients"), "clients_staff:list", areas=STAFF, parent="staff.clients",
         permission="view_clients"),
    item("staff.clients.add", _("Add a client"), "clients_staff:create", areas=STAFF, parent="staff.clients",
         permission="manage_clients"),
    item("staff.users", _("Users"), "console:users", areas=STAFF, section=_("People"), permission="view_users",
         icon="bi-person-badge"),
    item("staff.affiliates", _("Affiliates"), areas=STAFF, section=_("People"), icon="bi-share"),
    item("staff.affiliates.overview", _("Overview"), "affiliates_staff:overview", areas=STAFF, parent="staff.affiliates",
         permission="view_affiliates"),
    item("staff.affiliates.people", _("Affiliates"), "affiliates_staff:affiliates", areas=STAFF, parent="staff.affiliates",
         permission="view_affiliates"),
    item("staff.affiliates.commissions", _("Commissions"), "affiliates_staff:commissions", areas=STAFF,
         parent="staff.affiliates", permission="view_affiliates"),
    item("staff.affiliates.payouts", _("Payouts"), "affiliates_staff:payouts", areas=STAFF, parent="staff.affiliates",
         permission="view_affiliates"),

    item("staff.orders", _("Orders"), areas=STAFF, section=_("Commerce"), icon="bi-bag"),
    item("staff.orders.list", _("All orders"), "orders_staff:list", areas=STAFF, parent="staff.orders",
         permission="view_orders"),
    item("staff.orders.add", _("New order"), "orders_staff:new", areas=STAFF, parent="staff.orders",
         permission="manage_orders"),
    item("staff.billing", _("Billing"), areas=STAFF, section=_("Commerce"), icon="bi-credit-card-2-front"),
    item("staff.billing.overview", _("Overview"), "billing_staff:index", areas=STAFF, parent="staff.billing",
         permission="view_billing"),
    item("staff.billing.invoices", _("Invoices"), "billing_staff:invoice_list", areas=STAFF, parent="staff.billing",
         permission="view_billing"),
    item("staff.billing.transactions", _("Transactions"), "billing_staff:transaction_list", areas=STAFF,
         parent="staff.billing", permission="view_billing"),
    item("staff.billing.quotes", _("Quotes"), "billing_staff:quote_list", areas=STAFF, parent="staff.billing",
         permission="view_billing"),
    item("staff.billing.items", _("Billable items"), "billing_staff:billable_list", areas=STAFF,
         parent="staff.billing", permission="view_billing"),
    item("staff.billing.renewals", _("Renewals & upgrades"), "renewals_staff:list", areas=STAFF,
         parent="staff.billing", permission="view_billing"),
    item("staff.billing.coupons", _("Coupons"), "billing_staff:coupons", areas=STAFF, parent="staff.billing",
         permission="view_billing"),
    item("staff.catalog", _("Products"), areas=STAFF, section=_("Commerce"), icon="bi-box-seam"),
    item("staff.catalog.products", _("Products & plans"), "catalog_staff:product_list", areas=STAFF,
         parent="staff.catalog", permission="view_products"),
    item("staff.catalog.addons", _("Add-ons"), "catalog_staff:addon_list", areas=STAFF, parent="staff.catalog",
         permission="view_products"),

    item("staff.hosting", _("Hosting"), "hosting_staff:list", areas=STAFF, section=_("Operations"),
         permission="view_hosting", icon="bi-hdd-rack"),
    item("staff.domains", _("Domains"), areas=STAFF, section=_("Operations"), icon="bi-globe2"),
    item("staff.domains.list", _("Registrations"), "domains_staff:list", areas=STAFF, parent="staff.domains",
         permission="view_domains"),
    item("staff.domains.pricing", _("Pricing by extension"), "domains_staff:tld_list", areas=STAFF,
         parent="staff.domains", permission="view_domains"),
    item("staff.lifecycle", _("Lifecycle"), areas=STAFF, section=_("Operations"), icon="bi-arrow-repeat"),
    item("staff.lifecycle.overview", _("Overview"), "lifecycle_staff:overview", areas=STAFF, parent="staff.lifecycle",
         any_permission=("view_hosting", "view_domains")),
    item("staff.lifecycle.cancellations", _("Cancellations"), "lifecycle_staff:cancellations", areas=STAFF,
         parent="staff.lifecycle", any_permission=("view_hosting", "view_domains")),
    item("staff.support", _("Support"), areas=STAFF, section=_("Operations"), icon="bi-life-preserver"),
    item("staff.support.overview", _("Overview"), "support_staff:overview", areas=STAFF, parent="staff.support",
         permission="view_support"),
    item("staff.support.tickets", _("Tickets"), "support_staff:tickets", areas=STAFF, parent="staff.support",
         permission="view_support"),
    item("staff.support.new", _("New ticket"), "support_staff:ticket_new", areas=STAFF, parent="staff.support",
         permission="view_support"),
    item("staff.support.replies", _("Saved replies"), "support_staff:replies", areas=STAFF, parent="staff.support",
         permission="view_support"),
    item("staff.support.kb", _("Knowledge base"), "support_staff:kb", areas=STAFF, parent="staff.support",
         permission="view_support"),
    item("staff.support.departments", _("Departments"), "support_staff:departments", areas=STAFF,
         parent="staff.support", permission="view_support"),

    item("staff.reports", _("Reports"), "reports_staff:index", areas=STAFF, section=_("Insights"),
         permission="view_reports", icon="bi-graph-up"),
    item("staff.activity", _("Activity log"), "console:audit_log", areas=STAFF, section=_("Insights"),
         permission="view_audit_log", icon="bi-clock-history"),

    item("staff.providers", _("Providers"), areas=STAFF, section=_("System"), icon="bi-plug"),
    item("staff.providers.servers", _("Servers"), "catalog_staff:server_list", areas=STAFF, parent="staff.providers",
         permission="view_hosting"),
    item("staff.providers.registrar", _("Domain registrar"), "console:registrars", areas=STAFF,
         parent="staff.providers", permission="view_providers"),
    item("staff.providers.email", _("Email provider"), "console:email_providers", areas=STAFF,
         parent="staff.providers", permission="view_providers"),
    item("staff.providers.payments", _("Payment methods"), "billing_staff:payment_methods", areas=STAFF,
         parent="staff.providers", permission="view_billing"),
    item("staff.notifications", _("Notifications"), areas=STAFF, section=_("System"), icon="bi-envelope"),
    item("staff.notifications.stats", _("Delivery statistics"), "notifications_staff:overview", areas=STAFF,
         parent="staff.notifications", permission="view_settings"),
    item("staff.notifications.log", _("Email log"), "notifications_staff:emails", areas=STAFF,
         parent="staff.notifications", permission="view_settings"),
    item("staff.settings", _("Settings"), areas=STAFF, section=_("System"), icon="bi-sliders"),
    item("staff.settings.brand", _("Brand"), "brand_staff:settings", areas=STAFF, parent="staff.settings",
         permission="view_settings"),
    item("staff.settings.billing", _("Billing"), "billing_staff:settings", areas=STAFF, parent="staff.settings",
         permission="view_billing"),
    item("staff.settings.tax", _("Tax rules"), "billing_staff:tax_rules", areas=STAFF, parent="staff.settings",
         permission="view_billing"),
    item("staff.settings.lifecycle", _("Lifecycle timings"), "lifecycle_staff:settings", areas=STAFF,
         parent="staff.settings", permission="view_settings"),
    item("staff.settings.affiliates", _("Affiliate programme"), "affiliates_staff:settings", areas=STAFF,
         parent="staff.settings", permission="view_settings"),

    # --- Shared: notifications (top bar) and the account menu --------------------------------------------------------
    item("account.notifications", _("Notifications"), "notifications:inbox", areas=BOTH, position="right",
         badge="unread", icon="bi-bell"),
    item("account.menu", "{name}", areas=BOTH, position="account"),
    item("account.profile", _("Profile & security"), "accounts:profile", areas=BOTH, position="account",
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
    item("account.admin", _("Database admin (technical)"), "admin:index", areas=BOTH, position="account",
         parent="account.menu", requires="superuser", divider_before=True),
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
    section: str = ""
    mobile_tab: bool = False
    landing: str = ""   # where a group link goes: its first page

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
        badge = BADGES[entry.badge](request) if entry.badge else 0
        return Entry(key=entry.key, label=_label(entry, user), url=reverse(entry.url_name),
                     active=entry.url_name == current_name, badge=badge, icon=entry.icon,
                     divider_before=entry.divider_before, section=str(entry.section), mobile_tab=entry.mobile_tab,
                     landing=reverse(entry.url_name))

    menus = {position: [] for position in POSITIONS}
    same_screen_family = {position: [] for position in POSITIONS}  # groups whose pages share the current URL namespace
    for top in (e for e in ITEMS if area in e.areas and not e.parent):
        children = [c for c in ITEMS if c.parent == top.key and area in c.areas]
        if children or not top.url_name:
            shown = [leaf(c) for c in children if is_visible(user, c, has_client=has_client)]
            if not shown or not is_visible(user, top, has_client=has_client):
                continue
            group_ns = {c.url_name.split(":")[0] for c in children if ":" in c.url_name}
            group = Entry(key=top.key, label=_label(top, user), icon=top.icon, children=shown, landing=shown[0].url,
                          section=str(top.section), mobile_tab=top.mobile_tab, url=shown[0].url,
                          active=any(e.active for e in shown))
            if current_namespace and current_namespace in group_ns:
                same_screen_family[top.position].append(group)
            menus[top.position].append(group)
        elif is_visible(user, top, has_client=has_client):
            menus[top.position].append(leaf(top))
    # A page that is not itself an entry (an invoice, a ticket) keeps the first group of its family lit, and only one:
    # several groups can share a namespace (billing pages appear under Billing, Settings and Providers).
    for position, groups in same_screen_family.items():
        if groups and not any(e.active for e in menus[position]):
            groups[0].active = True
    return menus


def subnav(menus, path):
    """
    The pages of the group you are in, for the strip under the top bar: {"label", "children", "current"} or None. A group
    with one page has no strip. ``current`` is the child whose address is the longest start of this page's address, so
    an invoice keeps "Invoices" lit; a page that is under none of them lights none.
    """
    for entry in menus["main"]:
        if entry.is_group and entry.active and len(entry.children) > 1:
            best = ""
            for child in entry.children:
                if path.startswith(child.url) and len(child.url) > len(best):
                    best = child.url
            return {"label": entry.label, "children": entry.children, "current": best}
    return None


def pages(menus):
    """Every page the person may open, for "search or jump to": [{"label", "group", "url"}]."""
    found = []
    for entry in menus["main"]:
        if entry.is_group:
            found += [{"label": c.label, "group": entry.label, "url": c.url} for c in entry.children]
        else:
            found.append({"label": entry.label, "group": "", "url": entry.url})
    return found


def sections(entries):
    """The top-level entries grouped under their headings: [{"label", "key", "active", "entries"}]. A heading (People,
    Commerce ...) is a menu in the header; entries under no heading are direct links."""
    from django.utils.text import slugify

    grouped = []
    for entry in entries:
        if not grouped or grouped[-1]["label"] != entry.section:
            grouped.append({"label": entry.section, "key": slugify(entry.section) or "top", "active": False, "entries": []})
        grouped[-1]["entries"].append(entry)
        grouped[-1]["active"] = grouped[-1]["active"] or entry.active
    return grouped


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
