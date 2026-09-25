from django.urls import path

from . import views

app_name = "orders_staff"

urlpatterns = [
    path("", views.staff_order_list, name="list"),
    path("<int:pk>/", views.staff_order_detail, name="detail"),
    path("<int:pk>/cancel/", views.staff_order_cancel, name="cancel"),
]
