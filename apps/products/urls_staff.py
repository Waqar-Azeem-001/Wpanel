from django.urls import path

from . import views

app_name = "catalog_staff"

urlpatterns = [
    path("products/", views.staff_product_list, name="product_list"),
    path("products/new/", views.staff_product_create, name="product_create"),
    path("products/<slug:slug>/", views.staff_product_detail, name="product_detail"),
    path("products/<slug:slug>/edit/", views.staff_product_edit, name="product_edit"),
    path("products/<slug:slug>/status/", views.staff_product_status, name="product_status"),
    path("products/<slug:slug>/servers/", views.staff_product_servers, name="product_servers"),
    path("products/<slug:slug>/prices/", views.staff_product_price_add, name="product_price_add"),
    path("products/<slug:slug>/prices/<int:price_id>/toggle/", views.staff_product_price_toggle,
         name="product_price_toggle"),
    path("products/<slug:slug>/prices/<int:price_id>/remove/", views.staff_product_price_remove,
         name="product_price_remove"),

    path("addons/", views.staff_addon_list, name="addon_list"),
    path("addons/new/", views.staff_addon_create, name="addon_create"),
    path("addons/<slug:slug>/", views.staff_addon_detail, name="addon_detail"),
    path("addons/<slug:slug>/edit/", views.staff_addon_edit, name="addon_edit"),
    path("addons/<slug:slug>/status/", views.staff_addon_status, name="addon_status"),
    path("addons/<slug:slug>/prices/", views.staff_addon_price_add, name="addon_price_add"),
    path("addons/<slug:slug>/prices/<int:price_id>/toggle/", views.staff_addon_price_toggle,
         name="addon_price_toggle"),
    path("addons/<slug:slug>/prices/<int:price_id>/remove/", views.staff_addon_price_remove,
         name="addon_price_remove"),

    path("servers/", views.staff_server_list, name="server_list"),
    path("servers/new/", views.staff_server_create, name="server_create"),
    path("servers/<int:pk>/edit/", views.staff_server_edit, name="server_edit"),
    path("servers/<int:pk>/status/", views.staff_server_status, name="server_status"),
]
