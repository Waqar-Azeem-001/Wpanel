from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("", views.public_product_list, name="product_list"),
    path("<slug:slug>/", views.public_product_detail, name="product_detail"),
]
