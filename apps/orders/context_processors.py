from .models import CartItem, CartStatus


def cart_summary(request):
    """Number of items in the signed-in user's open cart(s), for the nav."""
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated):
        return {"cart_item_count": 0}
    return {"cart_item_count": CartItem.objects.filter(cart__user=user, cart__status=CartStatus.OPEN).count()}
