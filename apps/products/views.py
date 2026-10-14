"""Server-rendered catalog pages (staff and public). Rules live in ``services``."""
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.core import bulk
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError
from apps.core.web import run_action

from . import categories, forms, services
from .models import Addon, CatalogStatus, Product, Server, StoreCategory
from apps.branding.services import store_currency


def _apply_error(form, exc):
    if isinstance(exc, ValidationError) and hasattr(exc, "error_dict"):
        for field, errors in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    elif isinstance(exc, ValidationError):
        form.add_error(None, exc)
    else:
        form.add_error(None, exc.message)


def _redirect_back(request, item, action):
    try:
        message = action()
    except (ServiceError, ValidationError) as exc:
        messages.error(request, exc.message if isinstance(exc, ServiceError) else "; ".join(exc.messages))
    else:
        if message:
            messages.success(request, message)
    kind = "products" if isinstance(item, Product) else "addons"
    return redirect(f"catalog_staff:{kind[:-1]}_detail", slug=item.slug)


# --- Public catalog --------------------------------------------------------------------

def public_product_list(request):
    """The store: every category a visitor can shop for, or just one (``?category=vps``)."""
    slug = request.GET.get("category", "")
    everything = categories.sections()
    chosen = categories.get(slug)
    if chosen is not None and chosen.url_name:
        return redirect(chosen.url_name)
    if chosen is None and slug == "more":  # products nobody filed under a category
        chosen = next((c for _group, entries in everything for c, _items in entries if c.slug == "more"), None)
    shown = [(chosen.group, [(chosen, dict((c.slug, i) for _g, entries in everything for c, i in entries).get(chosen.slug, []))])] if chosen else everything
    nav = [c for _group, entries in everything for c, _items in entries]
    return render(request, "products/public/list.html", {"sections": shown, "chosen": chosen, "nav": nav,
                                                          "products": _cheapest_first(_active_products())})


def _active_products():
    return Product.objects.filter(status=CatalogStatus.ACTIVE).prefetch_related("prices")


def _cheapest_first(products):
    """Plans in the order a shopper reads them: by their lowest price, then name."""
    def lowest(product):
        prices = [p.price for p in product.prices.all()]
        return (min(prices) if prices else float("inf"), product.name)

    return sorted(products, key=lowest)


def public_home(request):
    """The front page for visitors: a search box for a domain, the plans, and why to choose us."""
    tiles = []
    for category in categories.active():
        entries = categories.items(category)
        lowest = [categories.lowest_price(e) for e in entries]
        lowest = [p for p in lowest if p is not None]
        tiles.append({"category": category, "count": len(entries), "from": min(lowest, key=lambda p: p.price) if lowest else None})
    return render(request, "public/home.html", {"products": _cheapest_first(_active_products())[:4], "tiles": tiles})


def public_addon_list(request):
    """The extras a customer can add to a plan, with their prices (they are chosen at checkout, on a hosting line)."""
    addons = Addon.objects.filter(status=CatalogStatus.ACTIVE).prefetch_related("prices").order_by("name")
    return render(request, "products/public/addons.html", {"addons": addons})


def public_product_detail(request, slug):
    product = get_object_or_404(Product.objects.prefetch_related("prices"), slug=slug, status=CatalogStatus.ACTIVE)
    prices = product.prices.filter(is_active=True)
    upsell = product.upsell_product if product.upsell_product and product.upsell_product.status == CatalogStatus.ACTIVE else None
    recommended = product.recommended_addons.filter(status=CatalogStatus.ACTIVE)
    # A link from an outside storefront can name the billing cycle ("annual", or "custom_4" / "custom:4").
    selected_cycle = (request.GET.get("cycle") or request.GET.get("billing_cycle") or "").replace("custom_", "custom:")
    return render(request, "products/public/detail.html", {"product": product, "prices": prices, "upsell": upsell,
                                                           "recommended_addons": recommended,
                                                           "selected_cycle": selected_cycle})


# --- Staff: products ---------------------------------------------------------------------

@portal_permission_required(perm("view_products"))
def staff_product_list(request):
    filter_form = forms.ProductFilterForm(request.GET or None)
    queryset = Product.objects.all()
    if filter_form.is_valid():
        queryset = services.search_catalog(queryset, filter_form.cleaned_data["q"])
        if filter_form.cleaned_data["status"]:
            queryset = queryset.filter(status=filter_form.cleaned_data["status"])
        if filter_form.cleaned_data["type"]:
            queryset = queryset.filter(type=filter_form.cleaned_data["type"])
    page = Paginator(queryset.order_by("name"), 25).get_page(request.GET.get("page"))
    template = "products/staff/_product_table.html" if request.headers.get("HX-Request") else \
        "products/staff/product_list.html"
    return render(request, template, {"filter_form": filter_form, "page": page})


@portal_permission_required(perm("manage_products"))
def staff_product_create(request):
    form = forms.ProductForm(request.POST or None, with_price=True)
    if request.method == "POST" and form.is_valid():
        try:
            product = services.create_product(request.user, form.cleaned_data, request=request)
        except (ServiceError, ValidationError) as exc:
            _apply_error(form, exc)
        else:
            messages.success(request, f"Product '{product.name}' created.")
            return redirect("catalog_staff:product_detail", slug=product.slug)
    return render(request, "products/staff/product_form.html", {"form": form, "title": "Add product"})


# --- Staff: store categories --------------------------------------------------------------------------------------

@portal_permission_required(perm("view_products"))
def staff_category_list(request):
    rows = StoreCategory.objects.annotate(n_products=Count("products", distinct=True), n_addons=Count("addons", distinct=True))
    return render(request, "products/staff/category_list.html", {
        "categories": rows.order_by("sort_order", "name"), "can_manage": request.user.has_perm(perm("manage_products"))})


@portal_permission_required(perm("manage_products"))
def staff_category_edit(request, pk=None):
    category = get_object_or_404(StoreCategory, pk=pk) if pk else None
    initial = {f: getattr(category, f) for f in services.CATEGORY_FIELDS} if category else None
    form = forms.CategoryForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            saved = services.save_category(request.user, category, form.cleaned_data, request=request)
        except (ServiceError, ValidationError) as exc:
            _apply_error(form, exc)
        else:
            messages.success(request, f"Category '{saved.name}' saved.")
            return redirect("catalog_staff:category_list")
    return render(request, "products/staff/category_form.html", {
        "form": form, "category": category, "title": f"Edit {category.name}" if category else "Add a category"})


@require_POST
@portal_permission_required(perm("manage_products"))
def staff_category_delete(request, pk):
    category = get_object_or_404(StoreCategory, pk=pk)

    def action():
        services.delete_category(request.user, category, request=request)
        return f"Category '{category.name}' deleted."

    return run_action(request, action, "catalog_staff:category_list")


@require_POST
@portal_permission_required(perm("manage_products"))
def staff_category_bulk(request):
    """"With selected": delete every ticked category that holds no products or add-ons (the same rule as one at a
    time - a category still in use is reported as a failure, not silently skipped)."""
    if request.POST.get("do") != "delete":
        messages.error(request, "Choose what to do with the selected categories.")
    else:
        bulk.run(request, StoreCategory.objects.all(), bulk.selected_ids(request),
                 lambda c: services.delete_category(request.user, c, request=request),
                 verb="categories deleted", label=lambda c: c.name)
    return bulk.back_to(request, "catalog_staff:category_list")


@portal_permission_required(perm("view_products"))
def staff_product_detail(request, slug):
    product = get_object_or_404(Product.objects.prefetch_related("prices", "servers"), slug=slug)
    can_manage = request.user.has_perm(perm("manage_products"))
    can_manage_hosting = request.user.has_perm(perm("manage_hosting"))
    context = {
        "product": product,
        "can_manage": can_manage,
        "status_form": forms.ProductStatusForm(initial={"status": product.status}),
        "price_form": forms.PriceForm(),
        "servers_form": forms.ProductServersForm(initial={"servers": product.servers.all()})
        if can_manage_hosting or can_manage else None,
    }
    if can_manage and request.user.has_perm(perm("view_providers")):
        from apps.storefronts.models import ExternalStorefront, StorefrontStatus

        context.update(
            storefronts=ExternalStorefront.objects.filter(status=StorefrontStatus.ACTIVE),
            published_storefront_ids=set(product.storefront_links.values_list("storefront_id", flat=True)),
            storefront_links=product.storefront_links.select_related("storefront"),
        )
    return render(request, "products/staff/product_detail.html", context)


@portal_permission_required(perm("manage_products"))
def staff_product_edit(request, slug):
    product = get_object_or_404(Product, slug=slug)
    initial = {
        "name": product.name, "type": product.type, "description": product.description,
        "whm_package_name": product.whm_package_name, "category": product.category_id,
        "auto_setup": product.auto_setup, "default_auto_renew": product.default_auto_renew,
        "upsell_product": product.upsell_product_id, "recommended_addons": list(product.recommended_addons.all()),
        "publish_externally": product.publish_externally,
    }
    form = forms.ProductForm(request.POST or None, initial=initial, existing_limits=product.ordered_limits())
    form.fields["upsell_product"].queryset = Product.objects.exclude(pk=product.pk)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_product(request.user, product, form.cleaned_data, request=request)
        except (ServiceError, ValidationError) as exc:
            _apply_error(form, exc)
        else:
            messages.success(request, "Product updated.")
            return redirect("catalog_staff:product_detail", slug=product.slug)
    return render(request, "products/staff/product_form.html",
                  {"form": form, "product": product, "title": f"Edit {product.name}"})


@require_POST
@portal_permission_required(perm("manage_products"))
def staff_product_status(request, slug):
    product = get_object_or_404(Product, slug=slug)
    form = forms.ProductStatusForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Choose a valid status.")
        services.set_product_status(request.user, product, form.cleaned_data["status"], request=request)
        return f"Status set to {product.get_status_display()}."

    return _redirect_back(request, product, action)


@require_POST
@portal_permission_required(perm("manage_products"))
def staff_product_servers(request, slug):
    product = get_object_or_404(Product, slug=slug)
    form = forms.ProductServersForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Choose valid servers.")
        services.set_product_servers(request.user, product, [s.pk for s in form.cleaned_data["servers"]],
                                     request=request)
        return "Server mapping updated."

    return _redirect_back(request, product, action)


@require_POST
@portal_permission_required(perm("manage_products"))
def staff_product_price_add(request, slug):
    product = get_object_or_404(Product, slug=slug)
    form = forms.PriceForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Enter a valid price.")
        services.set_price(request.user, product, request=request, **form.cleaned_data)
        return "Price saved."

    return _redirect_back(request, product, action)


@require_POST
@portal_permission_required(perm("manage_products"))
def staff_product_price_toggle(request, slug, price_id):
    product = get_object_or_404(Product, slug=slug)

    def action():
        entry = get_object_or_404(product.prices, pk=price_id)
        services.set_price_active(request.user, product, entry, not entry.is_active, request=request)
        return "Price updated."

    return _redirect_back(request, product, action)


@require_POST
@portal_permission_required(perm("manage_products"))
def staff_product_price_remove(request, slug, price_id):
    product = get_object_or_404(Product, slug=slug)

    def action():
        entry = get_object_or_404(product.prices, pk=price_id)
        services.remove_price(request.user, product, entry, request=request)
        return "Price removed."

    return _redirect_back(request, product, action)


# --- Staff: addons -------------------------------------------------------------------------

@portal_permission_required(perm("view_products"))
def staff_addon_list(request):
    filter_form = forms.CatalogFilterForm(request.GET or None)
    queryset = Addon.objects.all()
    if filter_form.is_valid():
        queryset = services.search_catalog(queryset, filter_form.cleaned_data["q"])
        if filter_form.cleaned_data["status"]:
            queryset = queryset.filter(status=filter_form.cleaned_data["status"])
    page = Paginator(queryset.order_by("name"), 25).get_page(request.GET.get("page"))
    return render(request, "products/staff/addon_list.html", {"filter_form": filter_form, "page": page})


@portal_permission_required(perm("manage_products"))
def staff_addon_create(request):
    form = forms.AddonForm(request.POST or None, with_price=True)
    if request.method == "POST" and form.is_valid():
        try:
            addon = services.create_addon(request.user, form.cleaned_data, request=request)
        except (ServiceError, ValidationError) as exc:
            _apply_error(form, exc)
        else:
            messages.success(request, f"Addon '{addon.name}' created.")
            return redirect("catalog_staff:addon_detail", slug=addon.slug)
    return render(request, "products/staff/addon_form.html", {"form": form, "title": "Add addon"})


@portal_permission_required(perm("view_products"))
def staff_addon_detail(request, slug):
    addon = get_object_or_404(Addon.objects.prefetch_related("prices"), slug=slug)
    return render(request, "products/staff/addon_detail.html", {
        "addon": addon,
        "can_manage": request.user.has_perm(perm("manage_products")),
        "status_form": forms.AddonStatusForm(initial={"status": addon.status}),
        "price_form": forms.AddonPriceForm(),
    })


@portal_permission_required(perm("manage_products"))
def staff_addon_edit(request, slug):
    addon = get_object_or_404(Addon, slug=slug)
    form = forms.AddonForm(request.POST or None, initial={"name": addon.name, "description": addon.description, "kind": addon.kind,
                                                                  "category": addon.category_id})
    if request.method == "POST" and form.is_valid():
        try:
            services.update_addon(request.user, addon, form.cleaned_data, request=request)
        except (ServiceError, ValidationError) as exc:
            _apply_error(form, exc)
        else:
            messages.success(request, "Addon updated.")
            return redirect("catalog_staff:addon_detail", slug=addon.slug)
    return render(request, "products/staff/addon_form.html", {"form": form, "addon": addon, "title": f"Edit {addon.name}"})


@require_POST
@portal_permission_required(perm("manage_products"))
def staff_addon_status(request, slug):
    addon = get_object_or_404(Addon, slug=slug)
    form = forms.AddonStatusForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Choose a valid status.")
        services.set_addon_status(request.user, addon, form.cleaned_data["status"], request=request)
        return f"Status set to {addon.get_status_display()}."

    return _redirect_back(request, addon, action)


@require_POST
@portal_permission_required(perm("manage_products"))
def staff_addon_price_add(request, slug):
    addon = get_object_or_404(Addon, slug=slug)
    form = forms.AddonPriceForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Enter a valid price.")
        services.set_price(request.user, addon, request=request, **form.cleaned_data)
        return "Price saved."

    return _redirect_back(request, addon, action)


@require_POST
@portal_permission_required(perm("manage_products"))
def staff_addon_price_toggle(request, slug, price_id):
    addon = get_object_or_404(Addon, slug=slug)

    def action():
        entry = get_object_or_404(addon.prices, pk=price_id)
        services.set_price_active(request.user, addon, entry, not entry.is_active, request=request)
        return "Price updated."

    return _redirect_back(request, addon, action)


@require_POST
@portal_permission_required(perm("manage_products"))
def staff_addon_price_remove(request, slug, price_id):
    addon = get_object_or_404(Addon, slug=slug)

    def action():
        entry = get_object_or_404(addon.prices, pk=price_id)
        services.remove_price(request.user, addon, entry, request=request)
        return "Price removed."

    return _redirect_back(request, addon, action)


# --- Staff: prices from WHMCS --------------------------------------------------------------------------------

SESSION_KEY = "whmcs_price_import"


@portal_permission_required(perm("manage_products"))
def staff_whmcs_import(request):
    """Step 1 asks for the WHMCS address and API login (used once, never kept) and shows a preview; step 2 writes the chosen prices."""
    from . import whmcs

    if request.method == "POST" and request.POST.get("step") == "apply":
        saved = request.session.get(SESSION_KEY) or {}
        rows = {r["pid"]: r for r in saved.get("rows", [])}
        choices = []
        for pid in request.POST.getlist("use"):
            product_id, row = request.POST.get(f"product_{pid}", ""), rows.get(pid)
            if row and product_id.isdigit() and row["prices"]:
                choices.append((int(product_id), row["prices"]))
        if not choices:
            messages.warning(request, "Tick at least one product and choose the WPanel product its prices go to.")
            return redirect("catalog_staff:whmcs_import") if not saved else render(request, "products/staff/whmcs_import.html", {
                "preview": services.whmcs_preview(saved["rows"]), "currency": saved.get("currency")})
        try:
            written = services.apply_whmcs_prices(request.user, choices, request=request)
        except (ServiceError, ValidationError) as exc:
            messages.error(request, getattr(exc, "message", None) or "; ".join(getattr(exc, "messages", [str(exc)])))
            return render(request, "products/staff/whmcs_import.html", {"preview": services.whmcs_preview(saved["rows"]), "currency": saved.get("currency")})
        request.session.pop(SESSION_KEY, None)
        messages.success(request, f"{written} price(s) set on {len(choices)} product(s) from WHMCS.")
        return redirect("catalog_staff:product_list")
    if request.method == "POST":
        try:
            rows, currency, codes = whmcs.fetch_products(request.POST.get("address", ""), request.POST.get("identifier", "").strip(),
                                                         request.POST.get("secret", ""), request.POST.get("currency", "").strip().upper())
        except whmcs.WhmcsError as exc:
            messages.error(request, str(exc))
            return render(request, "products/staff/whmcs_import.html", {"address": request.POST.get("address", ""),
                                                                         "identifier": request.POST.get("identifier", "")})
        request.session[SESSION_KEY] = {"rows": rows, "currency": currency}  # prices only: the login is not kept
        return render(request, "products/staff/whmcs_import.html", {"preview": services.whmcs_preview(rows), "currency": currency, "codes": codes,
                                                                     "store_currency": store_currency()})
    return render(request, "products/staff/whmcs_import.html", {})


# --- Staff: servers --------------------------------------------------------------------------

@portal_permission_required(perm("view_hosting"))
def staff_server_list(request):
    servers = Server.objects.prefetch_related("products")
    return render(request, "products/staff/server_list.html", {"servers": servers})


@portal_permission_required(perm("manage_hosting"))
def staff_server_create(request):
    form = forms.ServerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            server = services.create_server(request.user, form.cleaned_data, request=request)
        except (ServiceError, ValidationError) as exc:
            _apply_error(form, exc)
        else:
            messages.success(request, f"Server '{server.name}' added.")
            return redirect("catalog_staff:server_list")
    return render(request, "products/staff/server_form.html", {"form": form, "title": "Add server"})


@portal_permission_required(perm("manage_hosting"))
def staff_server_edit(request, pk):
    server = get_object_or_404(Server, pk=pk)
    form = forms.ServerForm(request.POST or None, initial={
        "name": server.name, "hostname": server.hostname, "ip_address": server.ip_address, "region": server.region,
        "max_accounts": server.max_accounts, "notes": server.notes,
        "kind": server.kind, "api_port": server.api_port, "api_username": server.api_username,
        "use_ssl": server.use_ssl, "verify_ssl": server.verify_ssl,
        "infra_provider": server.infra_provider, "infra_client_id": server.infra_client_id,
        "infra_api_user": server.infra_api_user,
    })
    if request.method == "POST" and form.is_valid():
        try:
            services.update_server(request.user, server, form.cleaned_data, request=request)
        except (ServiceError, ValidationError) as exc:
            _apply_error(form, exc)
        else:
            messages.success(request, "Server updated.")
            return redirect("catalog_staff:server_list")
    return render(request, "products/staff/server_form.html",
                  {"form": form, "server": server, "title": f"Edit {server.name}",
                   "status_form": forms.ServerStatusForm(initial={"status": server.status})})


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_server_test(request, pk):
    """Reach the server with its saved details (read-only) and say what it answered."""
    from apps.hosting import services as hosting_services

    server = get_object_or_404(Server, pk=pk)
    try:
        result = hosting_services.check_server(request.user, server, request=request)
    except ServiceError as exc:
        messages.error(request, f"Could not connect to {server.name}: {exc.message}")
    else:
        text = f"Connected to {server.name}."
        if result["version"]:
            text += f" Version {result['version']}."
        if result["packages"]:
            text += f" {len(result['packages'])} hosting package(s): {', '.join(result['packages'][:12])}."
        messages.success(request, text)
        for item in result["missing"]:
            messages.warning(request, f"Product '{item['product']}' creates package '{item['package']}', which this server does not have.")
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)  # back to the list the button was on
    return redirect("catalog_staff:server_edit", pk=server.pk)


@portal_permission_required(perm("manage_hosting"))
def staff_server_plans(request, pk):
    """Compare a server's own packages with the catalog and bring them in line: fix products pointing at a missing package, and turn new
    server packages into (hidden, unpriced) products."""
    from apps.hosting import services as hosting_services

    server = get_object_or_404(Server, pk=pk)
    try:
        result = hosting_services.check_server(request.user, server, request=request)
    except ServiceError as exc:
        messages.error(request, f"Could not read the plans from {server.name}: {exc.message}")
        return redirect("catalog_staff:server_list")
    packages = result["packages"]
    if request.method == "POST":
        remap = {int(k[len("remap_"):]): v for k, v in request.POST.items() if k.startswith("remap_") and k[len("remap_"):].isdigit()}
        try:
            refresh = [int(v) for v in request.POST.getlist("refresh") if v.isdigit()]
            remapped, created, refreshed = services.apply_server_plans(request.user, server, packages, remap=remap, create=request.POST.getlist("create"),
                                                                       details=result["details"], refresh=refresh, request=request)
        except (ServiceError, ValueError) as exc:
            messages.error(request, getattr(exc, "message", str(exc)))
        else:
            if remapped:
                messages.success(request, f"{len(remapped)} product(s) now use a package the server has.")
            if created:
                messages.success(request, f"{len(created)} product(s) created from the server's plans. They are hidden and have no price yet: "
                                          "set a price and publish them when ready.")
            if refreshed:
                messages.success(request, f"The features of {len(refreshed)} product(s) were copied from the server's plans.")
            if not remapped and not created and not refreshed:
                messages.info(request, "Nothing was changed.")
        return redirect("catalog_staff:server_plans", pk=server.pk)
    return render(request, "products/staff/server_plans.html", {"server": server, "version": result["version"], "packages": packages,
                                                                 **services.plans_report(server, packages, result["details"])})


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_server_infra_test(request, pk):
    """Ask the company that rents us the machine (for example Contabo) which machines this login can see."""
    from apps.hosting import services as hosting_services

    server = get_object_or_404(Server, pk=pk)
    try:
        result = hosting_services.check_infrastructure(request.user, server, request=request)
    except ServiceError as exc:
        messages.error(request, exc.message)
    else:
        found = result["instances"]
        names = "; ".join(f"{i['name'] or i['id']} ({i['ip'] or 'no IP'}, {i['status']})" for i in found[:8])
        messages.success(request, f"Connected to {result['provider']}. {len(found)} machine(s) on this login: {names or 'none'}.")
        if result["match"]:
            messages.success(request, f"This server's IP address matches '{result['match']['name'] or result['match']['id']}'"
                                      f" ({result['match']['status']}, {result['match']['region'] or 'region unknown'}).")
        elif server.ip_address:
            messages.warning(request, f"None of those machines has the IP address {server.ip_address} saved on this server.")
        elif result["suggested_ip"]:
            messages.info(request, f"This server has no IP address saved. The only machine here has {result['suggested_ip']}: "
                                   "enter it in the IP address field.")
    return redirect("catalog_staff:server_edit", pk=server.pk)


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_server_status(request, pk):
    server = get_object_or_404(Server, pk=pk)
    form = forms.ServerStatusForm(request.POST)
    if form.is_valid():
        try:
            services.set_server_status(request.user, server, form.cleaned_data["status"], request=request)
        except ServiceError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, f"Status set to {server.get_status_display()}.")
    return redirect("catalog_staff:server_edit", pk=server.pk)


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_server_delete(request, pk):
    server = get_object_or_404(Server, pk=pk)

    def action():
        services.delete_server(request.user, server, request=request)
        return f"Server '{server.name}' deleted."

    return run_action(request, action, "catalog_staff:server_list")


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_server_bulk(request):
    """"With selected": delete every ticked server with nothing hosted on it (the same rule as one at a time)."""
    if request.POST.get("do") != "delete":
        messages.error(request, "Choose what to do with the selected servers.")
    else:
        bulk.run(request, Server.objects.all(), bulk.selected_ids(request),
                 lambda s: services.delete_server(request.user, s, request=request),
                 verb="server(s) deleted", label=lambda s: s.name)
    return bulk.back_to(request, "catalog_staff:server_list")
