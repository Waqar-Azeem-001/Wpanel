"""Customer-facing routes, mounted at the site root (cart/, checkout/, account/orders/)."""
from django.urls import path

from . import views

app_name = "orders_customer"

urlpatterns = [
    path("cart/", views.cart_view, name="cart"),
    path("cart/add/hosting/", views.cart_add_hosting, name="cart_add_hosting"),
    path("cart/add/domain/", views.cart_add_domain, name="cart_add_domain"),
    path("cart/add/transfer/", views.cart_add_transfer, name="cart_add_transfer"),
    path("cart/add/addon/", views.cart_add_addon, name="cart_add_addon"),
    path("cart/items/<int:pk>/remove/", views.cart_remove, name="cart_remove"),
    path("cart/items/<int:pk>/upgrade/", views.cart_upgrade, name="cart_upgrade"),
    path("cart/items/<int:pk>/period/", views.cart_period, name="cart_period"),
    path("cart/addons/toggle/", views.cart_addon_toggle, name="cart_addon_toggle"),
    path("cart/coupon/", views.cart_coupon, name="cart_coupon"),
    path("cart/coupon/remove/", views.cart_coupon_remove, name="cart_coupon_remove"),
    path("checkout/", views.checkout_view, name="checkout"),
    path("account/orders/", views.order_list, name="list"),
    path("account/orders/<int:pk>/", views.order_detail, name="detail"),
    path("account/orders/<int:pk>/cancel/", views.order_cancel, name="cancel"),
]
