"""Staff pages for connected external storefronts, and the "which storefronts show this plan" control on a product."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError
from apps.core.web import ACTION_ERRORS, apply_form_error, error_text
from apps.products.models import Product

from . import forms, services
from .models import ExternalStorefront, StorefrontStatus

VIEW = perm("view_providers")
MANAGE = perm("manage_providers")


def _initial(storefront):
    return {f: getattr(storefront, f) for f in services.STOREFRONT_FIELDS}


@portal_permission_required(VIEW)
def staff_storefront_guide(request):
    """A step-by-step, screenshotted version of docs/storefront-connection-guide.md, inside the app itself -
    which value (public id, secret) comes from where, and what a future plugin would still need to build."""
    return render(request, "storefronts/staff/guide.html")


@portal_permission_required(VIEW)
def staff_storefront_list(request):
    rows = ExternalStorefront.objects.annotate(product_count=Count("published_products"))
    return render(request, "storefronts/staff/list.html", {
        "storefronts": rows, "can_manage": request.user.has_perm(MANAGE)})


@portal_permission_required(MANAGE)
def staff_storefront_create(request):
    form = forms.StorefrontForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            storefront, raw_secret = services.create_storefront(request.user, form.cleaned_data, request=request)
        except (ServiceError, ValidationError) as exc:
            apply_form_error(form, exc)
        else:
            messages.success(request, f"'{storefront.name}' connected. Its credential - shown once, copy it now: "
                                      f"public id {storefront.public_id} / secret {raw_secret}")
            return redirect("storefronts_staff:detail", pk=storefront.pk)
    return render(request, "storefronts/staff/form.html", {"form": form, "title": "Connect a storefront"})


@portal_permission_required(VIEW)
def staff_storefront_detail(request, pk):
    storefront = get_object_or_404(ExternalStorefront, pk=pk)
    can_manage = request.user.has_perm(MANAGE)
    edit_form = forms.StorefrontForm(initial=_initial(storefront)) if can_manage else None
    return render(request, "storefronts/staff/detail.html", {
        "storefront": storefront, "can_manage": can_manage, "form": edit_form,
        "published": storefront.published_products.select_related("product").order_by("sort_order", "product__name"),
        "has_webhook_secret": bool(storefront.webhook_secret_encrypted),
        "deliveries": storefront.webhook_deliveries.all()[:10]})


@require_POST
@portal_permission_required(MANAGE)
def staff_storefront_edit(request, pk):
    storefront = get_object_or_404(ExternalStorefront, pk=pk)
    form = forms.StorefrontForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Check the details and try again.")
    else:
        try:
            services.update_storefront(request.user, storefront, form.cleaned_data, request=request)
        except ACTION_ERRORS as exc:
            messages.error(request, error_text(exc))
        else:
            messages.success(request, "Saved.")
    return redirect("storefronts_staff:detail", pk=pk)


@require_POST
@portal_permission_required(MANAGE)
def staff_storefront_status(request, pk):
    storefront = get_object_or_404(ExternalStorefront, pk=pk)
    new_status = StorefrontStatus.DISABLED if storefront.is_active else StorefrontStatus.ACTIVE

    def action():
        services.set_storefront_status(request.user, storefront, new_status, request=request)
        return f"{storefront.name} is now {storefront.get_status_display().lower()}."

    return _run(request, action, "storefronts_staff:detail", pk=pk)


@require_POST
@portal_permission_required(MANAGE)
def staff_storefront_rotate(request, pk):
    storefront = get_object_or_404(ExternalStorefront, pk=pk)
    try:
        raw_secret = services.rotate_secret(request.user, storefront, request=request)
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
    else:
        messages.success(request, f"New secret for '{storefront.name}' - shown once, copy it now: {raw_secret}")
    return redirect("storefronts_staff:detail", pk=pk)


@require_POST
@portal_permission_required(MANAGE)
def staff_storefront_rotate_webhook_secret(request, pk):
    storefront = get_object_or_404(ExternalStorefront, pk=pk)
    try:
        raw_secret = services.rotate_webhook_secret(request.user, storefront, request=request)
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
    else:
        messages.success(request, f"Webhook signing secret for '{storefront.name}' - shown once, copy it now: {raw_secret}")
    return redirect("storefronts_staff:detail", pk=pk)


def _run(request, action, view_name, **kwargs):
    try:
        result = action()
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
    else:
        if result:
            messages.success(request, result)
    return redirect(view_name, **kwargs)


# --- Which storefronts show a plan (called from the product edit page) --------------------------------------------

@require_POST
@portal_permission_required(perm("manage_products"))
def staff_product_publish(request, slug):
    product = get_object_or_404(Product, slug=slug)
    storefront_ids = [int(v) for v in request.POST.getlist("storefronts") if v.isdigit()]
    try:
        services.set_product_storefronts(request.user, product, storefront_ids, request=request)
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
    else:
        messages.success(request, "Storefront publishing updated.")
    return redirect("catalog_staff:product_detail", slug=slug)


@require_POST
@portal_permission_required(perm("manage_products"))
def staff_product_link_update(request, slug, storefront_id):
    """Save one storefront's own title/description override, featured flag and sort order for this plan."""
    product = get_object_or_404(Product, slug=slug)
    storefront = get_object_or_404(ExternalStorefront, pk=storefront_id)
    sort_order = request.POST.get("sort_order", "")
    overrides = {
        "title_override": request.POST.get("title_override", "").strip(),
        "description_override": request.POST.get("description_override", "").strip(),
        "featured": request.POST.get("featured") == "on",
        "sort_order": int(sort_order) if sort_order.isdigit() else 100,
    }
    try:
        services.publish_product(request.user, product, storefront, request=request, **overrides)
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
    else:
        messages.success(request, f"Updated how this plan looks on '{storefront.name}'.")
    return redirect("catalog_staff:product_detail", slug=slug)
