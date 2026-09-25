from django.urls import path

from . import views

app_name = "billing_staff"

urlpatterns = [
    path("", views.index, name="index"),
    path("payment-methods/", views.payment_methods, name="payment_methods"),
    path("payment-methods/save/", views.payment_method_save, name="payment_method_save"),
    path("payment-methods/<int:pk>/status/", views.payment_method_status, name="payment_method_status"),
    path("tax-rules/", views.tax_rules, name="tax_rules"),
    path("tax-rules/save/", views.tax_rule_save, name="tax_rule_save"),
    path("tax-rules/<int:pk>/status/", views.tax_rule_status, name="tax_rule_status"),
    path("coupons/", views.coupons, name="coupons"),
    path("coupons/save/", views.coupon_save, name="coupon_save"),
    path("coupons/<int:pk>/status/", views.coupon_status, name="coupon_status"),
]
