"""Server-rendered cart, checkout and order pages (customer and staff). Rules live in ``services``/``pricing``."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.billing.models import PaymentMethod
from apps.clients.services import single_contact_client
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError
from apps.core.web import ACTION_ERRORS, apply_form_error, run_action
from apps.products.models import Addon, CatalogStatus, Product

from . import forms, pricing, services
from .models import CartItem, ItemKind, Order


def _form_error(form, fallback="Please check what you entered."):
    for errors in form.errors.values():
        return errors[0]
    return fallback


def _client_or_error(request):
    client = single_contact_client(request.user)
    if client is None:
        raise ServiceError("Ordering is available to customer accounts.", code="client_required")
    return client


# --- Cart --------------------------------------------------------------------------------------

def _addon_offers(priced):
    """For each hosting line: the active add-ons (and the prices) that can still be attached to it."""
    addons = list(Addon.objects.filter(status=CatalogStatus.ACTIVE).prefetch_related("prices"))
    attached = {}
    for line in priced.lines:
        if line.item.kind == ItemKind.ADDON:
            attached.setdefault(line.item.parent_id, set()).add(line.item.addon_id)
    offers = {}
    for line in priced.lines:
        if line.item.kind != ItemKind.HOSTING or not line.ok:
            continue
        options = []
        for addon in addons:
            if addon.pk in attached.get(line.item.pk, set()):
                continue
            rows = services.addon_options(line.item, addon)
            if rows:
                options.append({"addon": addon, "choices": [{
                    "value": row.option_value, "price": row.price, "setup_fee": row.setup_fee,
                    "label": "One-time" if row.billing_cycle == "one_time" else pricing.cycle_label(
                        row.billing_cycle, row.custom_months),
                } for row in rows]})
        offers[line.item.pk] = options
    return offers


@login_required
def cart_view(request):
    client = single_contact_client(request.user)
    if client is None:
        return render(request, "orders/customer/cart_unavailable.html")
    cart = services.get_open_cart(request.user, client)
    priced = pricing.price_cart(cart)
    offers = _addon_offers(priced)
    rows = [{"line": line, "offers": offers.get(line.item.pk, [])} for line in priced.lines]
    return render(request, "orders/customer/cart.html", {
        "cart": cart, "priced": priced, "rows": rows, "coupon_form": forms.CouponCodeForm(),
        "transfer_form": forms.AddTransferForm(),
    })


@require_POST
@login_required
def cart_add_hosting(request):
    form = forms.AddHostingForm(request.POST)

    def action():
        client = _client_or_error(request)
        if not form.is_valid():
            raise ServiceError(_form_error(form))
        product = Product.objects.filter(pk=form.cleaned_data["product"], status=CatalogStatus.ACTIVE).first()
        if product is None:
            raise ServiceError("This plan is no longer available.", code="product_unavailable")
        cycle, months = form.cleaned_data["cycle"]
        services.add_hosting(request.user, client, product, form.cleaned_data["domain"], cycle, months)
        return "Added to your cart."

    return run_action(request, action, "orders_customer:cart")


@require_POST
@login_required
def cart_add_domain(request):
    form = forms.AddDomainForm(request.POST)

    def action():
        client = _client_or_error(request)
        if not form.is_valid():
            raise ServiceError(_form_error(form))
        services.add_domain_registration(request.user, client, form.cleaned_data["domain"], form.cleaned_data["years"])
        return "Domain added to your cart."

    return run_action(request, action, "orders_customer:cart")


@require_POST
@login_required
def cart_add_transfer(request):
    form = forms.AddTransferForm(request.POST)

    def action():
        client = _client_or_error(request)
        if not form.is_valid():
            raise ServiceError(_form_error(form))
        services.add_domain_transfer(request.user, client, form.cleaned_data["domain"], form.cleaned_data["auth_code"])
        return "Transfer added to your cart."

    return run_action(request, action, "orders_customer:cart")


@require_POST
@login_required
def cart_add_addon(request):
    form = forms.AddAddonForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError(_form_error(form))
        parent = get_object_or_404(CartItem.objects.filter(cart__user=request.user), pk=form.cleaned_data["parent_item"])
        addon = get_object_or_404(Addon, pk=form.cleaned_data["addon"])
        cycle, months = form.cleaned_data["option"] or (None, 0)
        services.add_addon(request.user, parent, addon, cycle, months)
        return "Add-on added."

    return run_action(request, action, "orders_customer:cart")


@require_POST
@login_required
def cart_remove(request, pk):
    item = get_object_or_404(CartItem.objects.filter(cart__user=request.user), pk=pk)

    def action():
        services.remove_item(request.user, item)
        return "Removed from your cart."

    return run_action(request, action, "orders_customer:cart")


@require_POST
@login_required
def cart_coupon(request):
    form = forms.CouponCodeForm(request.POST)

    def action():
        client = _client_or_error(request)
        if not form.is_valid():
            raise ServiceError(_form_error(form))
        services.apply_coupon(request.user, services.get_open_cart(request.user, client), form.cleaned_data["code"])
        return "Coupon applied."

    return run_action(request, action, "orders_customer:cart")


@require_POST
@login_required
def cart_coupon_remove(request):
    def action():
        client = _client_or_error(request)
        services.remove_coupon(request.user, services.get_open_cart(request.user, client))
        return "Coupon removed."

    return run_action(request, action, "orders_customer:cart")


# --- Checkout ------------------------------------------------------------------------------------

@login_required
def checkout_view(request):
    client = single_contact_client(request.user)
    if client is None:
        return render(request, "orders/customer/cart_unavailable.html")
    cart = services.get_open_cart(request.user, client)
    priced = pricing.price_cart(cart)
    if not priced.lines:
        messages.info(request, "Your cart is empty.")
        return redirect("orders_customer:cart")

    form = forms.CheckoutForm(request.POST if request.method == "POST" else None)  # bound even if the body is empty
    if request.method == "POST" and form.is_valid():
        try:
            order = services.checkout(request.user, cart, payment_method_code=form.cleaned_data["payment_method"],
                                      notes=form.cleaned_data["notes"], request=request)
        except ACTION_ERRORS as exc:
            apply_form_error(form, exc)
        else:
            messages.success(request, f"Order {order.reference} placed. Follow the payment instructions below.")
            return redirect("orders_customer:detail", pk=order.pk)
    return render(request, "orders/customer/checkout.html", {
        "cart": cart, "priced": priced, "form": form, "client": client,
        "methods": PaymentMethod.objects.filter(is_active=True),
    })


# --- Customer orders ----------------------------------------------------------------------------------

@login_required
def order_list(request):
    orders = Order.objects.filter(client__contacts__user=request.user).select_related("client").distinct()
    return render(request, "orders/customer/list.html", {"orders": orders})


@login_required
def order_detail(request, pk):
    order = get_object_or_404(services.visible_orders_for_user(request.user).prefetch_related("items", "payment_method"),
                              pk=pk)
    return render(request, "orders/customer/detail.html", {
        "order": order, "payment_method": order.payment_method, "cancel_form": forms.CancelOrderForm(),
    })


@require_POST
@login_required
def order_cancel(request, pk):
    order = get_object_or_404(services.visible_orders_for_user(request.user), pk=pk)
    form = forms.CancelOrderForm(request.POST)

    def action():
        reason = form.cleaned_data.get("reason", "") if form.is_valid() else ""
        services.cancel_order(request.user, order, reason=reason, request=request)
        return "Order cancelled."

    return run_action(request, action, "orders_customer:detail", pk=pk)


# --- Staff ------------------------------------------------------------------------------------------------

@portal_permission_required(perm("view_orders"))
def staff_order_list(request):
    filter_form = forms.OrderFilterForm(request.GET or None)
    queryset = Order.objects.select_related("client")
    if filter_form.is_valid():
        queryset = services.search_orders(queryset, filter_form.cleaned_data["q"])
        if filter_form.cleaned_data["status"]:
            queryset = queryset.filter(status=filter_form.cleaned_data["status"])
    page = Paginator(queryset.order_by("-created_at", "-id"), 25).get_page(request.GET.get("page"))
    return render(request, "orders/staff/list.html", {"filter_form": filter_form, "page": page})


@portal_permission_required(perm("view_orders"))
def staff_order_detail(request, pk):
    order = get_object_or_404(Order.objects.select_related("client", "payment_method").prefetch_related("items"),
                              pk=pk)
    return render(request, "orders/staff/detail.html", {
        "order": order, "can_manage": request.user.has_perm(perm("manage_orders")),
        "cancel_form": forms.CancelOrderForm(),
    })


@require_POST
@portal_permission_required(perm("manage_orders"))
def staff_order_cancel(request, pk):
    order = get_object_or_404(Order, pk=pk)
    form = forms.CancelOrderForm(request.POST)

    def action():
        reason = form.cleaned_data.get("reason", "") if form.is_valid() else ""
        services.cancel_order(request.user, order, reason=reason, request=request)
        return "Order cancelled."

    return run_action(request, action, "orders_staff:detail", pk=pk)
