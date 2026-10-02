from django.urls import path

from . import views

app_name = "storefronts_staff"

urlpatterns = [
    path("", views.staff_storefront_list, name="list"),
    path("new/", views.staff_storefront_create, name="create"),
    path("<int:pk>/", views.staff_storefront_detail, name="detail"),
    path("<int:pk>/edit/", views.staff_storefront_edit, name="edit"),
    path("<int:pk>/status/", views.staff_storefront_status, name="status"),
    path("<int:pk>/rotate/", views.staff_storefront_rotate, name="rotate"),
    path("<int:pk>/webhook-secret/", views.staff_storefront_rotate_webhook_secret, name="rotate_webhook_secret"),
    path("products/<slug:slug>/publish/", views.staff_product_publish, name="product_publish"),
    path("products/<slug:slug>/publish/<int:storefront_id>/", views.staff_product_link_update, name="product_link_update"),
]
