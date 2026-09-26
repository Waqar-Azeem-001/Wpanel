"""Server-rendered cart, checkout and order pages (customer and staff). Rules live in ``services``/``pricing``."""
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts import services as accounts_services
from apps.accounts.roles import perm
from apps.billing.models import PaymentMethod
from apps.clients import services as client_services
from apps.clients.services import single_contact_client
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError
from apps.core.web import ACTION_ERRORS, apply_form_error, error_text, query_id, run_action
from apps.products.models import Addon, CatalogStatus, Product

from . import forms, guest, lifecycle, pricing, services, staff_actions
from .models import CartItem, ItemKind, Order, OrderStatus


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
# Anyone can shop: a visitor's cart is a guest cart kept in their session (see ``guest``), a signed-in customer's is their own.
# Only checkout asks who they are, and it can create the account in the same step.

def _addon_offers(priced, recommended=frozenset()):
    """For each hosting line: the active add-ons (and the prices) that can still be attached to it, recommended ones first."""
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
                options.append({"addon": addon, "recommended": addon.pk in recommended, "choices": [{
                    "value": row.option_value, "price": row.price, "setup_fee": row.setup_fee,
                    "label": "One-time" if row.billing_cycle == "one_time" else pricing.cycle_label(
                        row.billing_cycle, row.custom_months),
                } for row in rows]})
        options.sort(key=lambda o: (not o["recommended"], o["addon"].name))
        offers[line.item.pk] = options
    return offers


def _shopper(request, *, create=False):
    """
    (actor, client, cart) for whoever is shopping: a signed-in customer (their own cart is fetched by the services), or a visitor
    (a guest cart, created on first add when ``create``). Staff and customers with no single account cannot shop here.
    """
    if request.user.is_authenticated:
        return request.user, _client_or_error(request), None
    return None, None, (guest.current_or_create(request) if create else guest.current(request))


def _current_cart(request):
    """The cart being looked at, or None (an empty visitor). Raises nothing for a visitor; a customer with no account is sent away."""
    if request.user.is_authenticated:
        client = single_contact_client(request.user)
        return services.get_open_cart(request.user, client) if client is not None else False
    return guest.current(request)


def cart_view(request):
    cart = _current_cart(request)
    if cart is False:
        return render(request, "orders/customer/cart_unavailable.html")
    country = request.GET.get("country", "").strip().upper()[:2]
    if cart is None:
        return render(request, "orders/customer/cart.html", {"rows": [], "guest": True})
    priced = pricing.price_cart(cart, country=country)
    offers = _addon_offers(priced, services.recommended_addon_ids(cart))
    rows = [{"line": line, "offers": offers.get(line.item.pk, []),
             "upgrade": services.upgrade_options(line.item) if line.ok else None} for line in priced.lines]
    return render(request, "orders/customer/cart.html", {
        "cart": cart, "priced": priced, "rows": rows, "coupon_form": forms.CouponCodeForm(),
        "transfer_form": forms.AddTransferForm(), "guest": cart.is_guest,
        "domain_offers": services.domain_offers(cart, priced)})


def _add(request, form, add, message):
    """Run one add-to-cart action for a customer or a visitor, then show the cart."""

    def action():
        actor, client, cart = _shopper(request, create=True)
        if not form.is_valid():
            raise ServiceError(_form_error(form))
        add(actor, client, cart)
        return message

    return run_action(request, action, "orders_customer:cart")


@require_POST
def cart_add_hosting(request):
    form = forms.AddHostingForm(request.POST)

    def add(actor, client, cart):
        product = Product.objects.filter(pk=form.cleaned_data["product"], status=CatalogStatus.ACTIVE).first()
        if product is None:
            raise ServiceError("This plan is no longer available.", code="product_unavailable")
        cycle, months = form.cleaned_data["cycle"]
        services.add_hosting(actor, client, product, form.cleaned_data["domain"], cycle, months, cart=cart)

    return _add(request, form, add, "Added to your cart.")


@require_POST
def cart_add_domain(request):
    form = forms.AddDomainForm(request.POST)
    return _add(request, form, lambda actor, client, cart: services.add_domain_registration(
        actor, client, form.cleaned_data["domain"], form.cleaned_data["years"], cart=cart), "Domain added to your cart.")


@require_POST
def cart_add_transfer(request):
    form = forms.AddTransferForm(request.POST)
    return _add(request, form, lambda actor, client, cart: services.add_domain_transfer(
        actor, client, form.cleaned_data["domain"], form.cleaned_data["auth_code"], cart=cart), "Transfer added to your cart.")


def _own_items(request):
    """The cart items this shopper may act on: their own cart's, or the visitor's guest cart's."""
    if request.user.is_authenticated:
        return CartItem.objects.filter(cart__user=request.user)
    cart = guest.current(request)
    return CartItem.objects.filter(cart=cart) if cart is not None else CartItem.objects.none()


@require_POST
def cart_add_addon(request):
    form = forms.AddAddonForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError(_form_error(form))
        parent = get_object_or_404(_own_items(request), pk=form.cleaned_data["parent_item"])
        addon = get_object_or_404(Addon, pk=form.cleaned_data["addon"])
        cycle, months = form.cleaned_data["option"] or (None, 0)
        services.add_addon(request.user if request.user.is_authenticated else None, parent, addon, cycle, months)
        return "Add-on added."

    return run_action(request, action, "orders_customer:cart")


@require_POST
def cart_remove(request, pk):
    item = get_object_or_404(_own_items(request), pk=pk)

    def action():
        services.remove_item(request.user if request.user.is_authenticated else None, item)
        return "Removed from your cart."

    return run_action(request, action, "orders_customer:cart")


@require_POST
def cart_upgrade(request, pk):
    """The upsell: swap this plan for the bigger one staff suggested, keeping the domain, cycle and add-ons."""
    item = get_object_or_404(_own_items(request).select_related("cart", "product"), pk=pk)

    def action():
        services.upgrade_item(request.user if request.user.is_authenticated else None, item, request=request)
        return f"Upgraded to {item.product.name}."

    return run_action(request, action, "orders_customer:cart")


def _coupon_target(request):
    cart = _current_cart(request)
    if cart is False:
        raise ServiceError("Ordering is available to customer accounts.", code="client_required")
    if cart is None:
        raise ServiceError("Your cart is empty.", code="cart_empty")
    return request.user if request.user.is_authenticated else None, cart


@require_POST
def cart_coupon(request):
    form = forms.CouponCodeForm(request.POST)

    def action():
        actor, cart = _coupon_target(request)
        if not form.is_valid():
            raise ServiceError(_form_error(form))
        services.apply_coupon(actor, cart, form.cleaned_data["code"])
        return "Coupon applied."

    return run_action(request, action, "orders_customer:cart")


@require_POST
def cart_coupon_remove(request):
    def action():
        actor, cart = _coupon_target(request)
        services.remove_coupon(actor, cart)
        return "Coupon removed."

    return run_action(request, action, "orders_customer:cart")


# --- Checkout ------------------------------------------------------------------------------------

def _payment_methods():
    return PaymentMethod.objects.filter(is_active=True)


def checkout_view(request):
    """A signed-in customer confirms and pays; a visitor gives their details and gets an account in the same step."""
    if not request.user.is_authenticated:
        return _guest_checkout(request)
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
        "cart": cart, "priced": priced, "form": form, "client": client, "guest": False, "methods": _payment_methods()})


def _guest_checkout(request):
    cart = guest.current(request)
    if cart is None or not cart.items.exists():
        messages.info(request, "Your cart is empty.")
        return redirect("orders_customer:cart")
    posted = request.method == "POST"
    refresh = posted and request.POST.get("action") == "refresh"  # only the country changed: show the tax, keep what was typed
    country = (request.POST.get("country") if posted else request.GET.get("country", "")) or ""
    priced = pricing.price_cart(cart, country=country.strip().upper()[:2])
    if refresh:
        form = forms.GuestCheckoutForm(initial={k: v for k, v in request.POST.items() if "password" not in k})
    else:
        form = forms.GuestCheckoutForm(request.POST if posted else None, initial={"country": country})
    if posted and not refresh and form.is_valid():
        data = form.cleaned_data
        try:
            pricing.price_cart(cart, strict=True, verify_availability=True)  # refuse a bad cart before any account exists
            user = accounts_services.register_user(
                email=data["email"], password=data["password"], first_name=data["first_name"],
                last_name=data["last_name"], phone=data["phone"], company_name=data["company_name"],
                referral_code=request.COOKIES.get("wp_ref", ""), request=request)
        except ServiceError as exc:
            if exc.code == "email_taken":
                form.add_error("email", "An account with this email already exists. Sign in to use it, and your cart comes with you.")
            else:
                apply_form_error(form, exc)
        except ValidationError as exc:
            form.add_error("password", exc)
        else:
            return _finish_guest_checkout(request, user, data)
    return render(request, "orders/customer/checkout.html", {
        "cart": cart, "priced": priced, "form": form, "guest": True, "methods": _payment_methods(),
        "login_url": f"{reverse('accounts:login')}?next={reverse('orders_customer:checkout')}"})


def _finish_guest_checkout(request, user, data):
    """The account exists: sign in (which brings the cart across), record the country, and place the order."""
    login(request, user)  # the sign-in signal moves the guest cart into the new account's cart
    client = single_contact_client(user)
    try:
        client_services.update_client(user, client, {"country": data["country"], "phone": data["phone"]}, request=request)
        cart = services.get_open_cart(user, client)
        order = services.checkout(user, cart, payment_method_code=data["payment_method"], notes=data["notes"], request=request)
    except ACTION_ERRORS as exc:
        messages.error(request, f"Your account was created, but the order was not placed: {error_text(exc)} "
                                "Your cart is kept; fix it and check out again.")
        return redirect("orders_customer:cart")
    messages.success(request, f"Welcome! Your account is ready and order {order.reference} is placed. Check your inbox to verify "
                              "your email address, and follow the payment instructions below.")
    response = redirect("orders_customer:detail", pk=order.pk)
    response.delete_cookie("wp_ref")
    return response


# --- Customer orders ----------------------------------------------------------------------------------

@login_required
def order_list(request):
    orders = Order.objects.filter(client__contacts__user=request.user).select_related("client").distinct()
    return render(request, "orders/customer/list.html", {"orders": orders})


@login_required
def order_detail(request, pk):
    order = get_object_or_404(services.visible_orders_for_user(request.user).prefetch_related(
        "items", "payment_method"), pk=pk)
    return render(request, "orders/customer/detail.html", {
        "order": order, "payment_method": order.payment_method, "cancel_form": forms.CancelOrderForm(),
        "items": order.items.select_related("hosting_account", "domain"),
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
    group = request.GET.get("group", "")
    if group in lifecycle.GROUPS:
        queryset = queryset.filter(status__in=lifecycle.GROUPS[group])
    else:
        group = ""
    if filter_form.is_valid():
        queryset = services.search_orders(queryset, filter_form.cleaned_data["q"])
        if filter_form.cleaned_data["status"]:
            queryset = queryset.filter(status=filter_form.cleaned_data["status"])
    counts = {key: Order.objects.filter(status__in=statuses).count() for key, statuses in lifecycle.GROUPS.items()}
    counts[""] = Order.objects.count()
    tabs = [{"key": key, "label": label, "count": counts[key]} for key, label in lifecycle.GROUP_LABELS.items()]
    page = Paginator(queryset.order_by("-created_at", "-id"), 25).get_page(request.GET.get("page"))
    return render(request, "orders/staff/list.html", {
        "filter_form": filter_form, "page": page, "tabs": tabs, "group": group,
        "can_manage": request.user.has_perm(perm("manage_orders"))})


@portal_permission_required(perm("view_orders"))
def staff_order_detail(request, pk):
    order = get_object_or_404(Order.objects.select_related("client", "payment_method").prefetch_related("items"),
                              pk=pk)
    return render(request, "orders/staff/detail.html", {
        "order": order, "can_manage": request.user.has_perm(perm("manage_orders")),
        "cancel_form": forms.CancelOrderForm(), "reason_form": forms.ReasonForm(),
        "items": order.items.select_related("hosting_account", "domain"), "timeline": lifecycle.timeline(order),
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


def _staff_order_action(request, pk, function, message, *, needs_reason=False):
    order = get_object_or_404(Order, pk=pk)
    form = forms.ReasonForm(request.POST)

    def action():
        kwargs = {"request": request}
        if needs_reason:
            kwargs["reason"] = form.cleaned_data.get("reason", "") if form.is_valid() else ""
        function(request.user, order, **kwargs)
        return message

    return run_action(request, action, "orders_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_orders"))
def staff_order_fraud(request, pk):
    return _staff_order_action(request, pk, staff_actions.mark_fraud, "Order held as fraud.", needs_reason=True)


@require_POST
@portal_permission_required(perm("manage_orders"))
def staff_order_clear_fraud(request, pk):
    return _staff_order_action(request, pk, staff_actions.clear_fraud, "Fraud hold released.", needs_reason=True)


@require_POST
@portal_permission_required(perm("manage_orders"))
def staff_order_retry(request, pk):
    order = get_object_or_404(Order, pk=pk)

    def action():
        result = staff_actions.retry_fulfilment(request.user, order, request=request)
        if result.status == OrderStatus.FAILED:
            raise ServiceError(f"Some lines still could not be fulfilled: {result.status_reason}")
        return "Fulfilment completed. The order is active."

    return run_action(request, action, "orders_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_orders"))
def staff_order_suspend(request, pk):
    return _staff_order_action(request, pk, staff_actions.suspend_order, "Order suspended.", needs_reason=True)


@require_POST
@portal_permission_required(perm("manage_orders"))
def staff_order_unsuspend(request, pk):
    return _staff_order_action(request, pk, staff_actions.unsuspend_order, "Order reactivated.")


@require_POST
@portal_permission_required(perm("manage_orders"))
def staff_order_terminate(request, pk):
    return _staff_order_action(request, pk, staff_actions.terminate_order, "Order terminated.", needs_reason=True)


# --- Staff: Add Order (build an order on a client's behalf) ------------------------------------------------

@portal_permission_required(perm("manage_orders"))
def staff_order_new(request):
    from apps.clients.models import Client
    from apps.clients.services import search_clients

    client_id = query_id(request, "client")
    if client_id is None:
        term = request.GET.get("q", "").strip()
        clients = search_clients(Client.objects.all(), term).order_by("company_name", "first_name", "id")[:15] \
            if term else []
        return render(request, "billing/staff/client_picker.html", {
            "title": "Orders", "create_url": "orders_staff:new", "clients": clients, "q": term})
    client = get_object_or_404(Client, pk=client_id)
    here = f"{reverse('orders_staff:new')}?client={client.pk}"
    try:
        cart = services.get_open_cart(request.user, client)
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect("orders_staff:list")

    if request.method == "POST":
        do = request.POST.get("action", "")

        def run():
            if do == "add_hosting":
                form = forms.StaffAddHostingForm(request.POST)
                if not form.is_valid():
                    raise ServiceError(_form_error(form))
                data = form.cleaned_data
                services.add_hosting(request.user, client, data["product"], data["domain"], data["cycle"], 0)
                return "Hosting added."
            if do == "add_domain":
                form = forms.StaffAddDomainForm(request.POST)
                if not form.is_valid():
                    raise ServiceError(_form_error(form))
                services.add_domain_registration(request.user, client, form.cleaned_data["domain"],
                                                 form.cleaned_data["years"])
                return "Domain added."
            if do == "add_transfer":
                form = forms.AddTransferForm(request.POST)
                if not form.is_valid():
                    raise ServiceError(_form_error(form))
                services.add_domain_transfer(request.user, client, form.cleaned_data["domain"],
                                             form.cleaned_data["auth_code"])
                return "Transfer added."
            if do == "coupon":
                form = forms.CouponCodeForm(request.POST)
                if not form.is_valid():
                    raise ServiceError(_form_error(form))
                services.apply_coupon(request.user, cart, form.cleaned_data["code"])
                return "Coupon applied."
            if do == "remove_coupon":
                services.remove_coupon(request.user, cart)
                return "Coupon removed."
            if do == "remove_item":
                item = get_object_or_404(cart.items.all(), pk=request.POST.get("item") or 0)
                services.remove_item(request.user, item)
                return "Removed."
            if do == "checkout":
                form = forms.CheckoutForm(request.POST)
                if not form.is_valid():
                    raise ServiceError(_form_error(form))
                order = services.checkout(request.user, cart, payment_method_code=form.cleaned_data["payment_method"],
                                          notes=form.cleaned_data["notes"], request=request)
                messages.success(request, f"Order {order.reference} placed for {client.display_name}.")
                return order
            raise ServiceError("Choose an action.")

        try:
            result = run()
        except ACTION_ERRORS as exc:
            messages.error(request, error_text(exc))
        else:
            if isinstance(result, Order):
                return redirect("orders_staff:detail", pk=result.pk)
            messages.success(request, result)
        return redirect(here)

    priced = pricing.price_cart(cart)
    return render(request, "orders/staff/new.html", {
        "client": client, "cart": cart, "priced": priced, "here": here,
        "hosting_form": forms.StaffAddHostingForm(), "domain_form": forms.StaffAddDomainForm(),
        "transfer_form": forms.AddTransferForm(), "coupon_form": forms.CouponCodeForm(),
        "checkout_form": forms.CheckoutForm(), "section": "orders"})
