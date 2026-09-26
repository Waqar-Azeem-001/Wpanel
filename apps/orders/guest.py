"""
The visitor's cart, before they have an account.

Anyone may put a plan or a domain in a cart. The cart is a ``Cart`` row with no user and no client, found through a random
token kept in the visitor's session (nothing else identifies it, so nobody can open another visitor's cart). It holds selections
only, like every cart. When the visitor signs in, or creates an account while checking out, ``claim`` moves the selections into
their account's own cart.
"""
import uuid

from django.db.models import Count

from .models import Cart, CartStatus

SESSION_KEY = "guest_cart"


def _token(request):
    raw = request.session.get(SESSION_KEY)
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


def current(request):
    """The visitor's open guest cart, or None (never created just by looking)."""
    token = _token(request)
    if token is None:
        return None
    return Cart.objects.filter(guest_token=token, user__isnull=True, status=CartStatus.OPEN).first()


def current_or_create(request):
    cart = current(request)
    if cart is None:
        cart = Cart.objects.create(guest_token=uuid.uuid4())
        request.session[SESSION_KEY] = str(cart.guest_token)
    return cart


def item_count(request):
    """How many lines the visitor's cart has (for the header); a cheap count that never creates anything."""
    token = _token(request)
    if token is None:
        return 0
    row = Cart.objects.filter(guest_token=token, user__isnull=True, status=CartStatus.OPEN).aggregate(n=Count("items"))
    return row["n"] or 0


def forget(request):
    request.session.pop(SESSION_KEY, None)


def claim(request, user):
    """
    Called when someone signs in or has just created an account: their guest cart (if any) joins their own cart. Only a customer
    with exactly one client account can order, so anyone else (staff, a customer of several accounts) leaves it as it is.
    Returns the number of lines moved.
    """
    from apps.clients.services import single_contact_client

    from . import services

    cart = current(request)
    if cart is None or not user.is_authenticated or user.is_staff:
        return 0
    client = single_contact_client(user)
    if client is None:
        return 0
    moved = cart.items.count()
    services.adopt_guest_cart(cart, user, client)
    forget(request)
    return moved
