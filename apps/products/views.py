"""Server-rendered catalog pages (staff and public). Rules live in ``services``."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError

from . import forms, services
from .models import Addon, CatalogStatus, Product, Server


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
    products = Product.objects.filter(status=CatalogStatus.ACTIVE).prefetch_related("prices")
    return render(request, "products/public/list.html", {"products": products})


def public_product_detail(request, slug):
    product = get_object_or_404(Product.objects.prefetch_related("prices"), slug=slug, status=CatalogStatus.ACTIVE)
    prices = product.prices.filter(is_active=True)
    return render(request, "products/public/detail.html", {"product": product, "prices": prices})


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
    form = forms.ProductForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            product = services.create_product(request.user, form.cleaned_data, request=request)
        except (ServiceError, ValidationError) as exc:
            _apply_error(form, exc)
        else:
            messages.success(request, f"Product '{product.name}' created.")
            return redirect("catalog_staff:product_detail", slug=product.slug)
    return render(request, "products/staff/product_form.html", {"form": form, "title": "Add product"})


@portal_permission_required(perm("view_products"))
def staff_product_detail(request, slug):
    product = get_object_or_404(Product.objects.prefetch_related("prices", "servers"), slug=slug)
    can_manage = request.user.has_perm(perm("manage_products"))
    can_manage_hosting = request.user.has_perm(perm("manage_hosting"))
    return render(request, "products/staff/product_detail.html", {
        "product": product,
        "can_manage": can_manage,
        "status_form": forms.ProductStatusForm(initial={"status": product.status}),
        "price_form": forms.PriceForm(),
        "servers_form": forms.ProductServersForm(initial={"servers": product.servers.all()})
        if can_manage_hosting or can_manage else None,
    })


@portal_permission_required(perm("manage_products"))
def staff_product_edit(request, slug):
    product = get_object_or_404(Product, slug=slug)
    initial = {
        "name": product.name, "type": product.type, "description": product.description,
        "resource_limits": product.resource_limits, "whm_package_name": product.whm_package_name,
        "auto_setup": product.auto_setup, "default_auto_renew": product.default_auto_renew,
    }
    form = forms.ProductForm(request.POST or None, initial=initial)
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
    form = forms.AddonForm(request.POST or None)
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
    form = forms.AddonForm(request.POST or None, initial={"name": addon.name, "description": addon.description})
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
        "name": server.name, "hostname": server.hostname, "ip_address": server.ip_address,
        "max_accounts": server.max_accounts, "notes": server.notes,
        "kind": server.kind, "api_port": server.api_port, "api_username": server.api_username,
        "use_ssl": server.use_ssl, "verify_ssl": server.verify_ssl,
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
