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
    path("products/<slug:slug>/publish/", views.staff_product_publish, name="product_publish"),
]
