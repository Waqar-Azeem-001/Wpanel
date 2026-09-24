from django.urls import path

from . import views

app_name = "clients_customer"

urlpatterns = [
    path("", views.my_clients, name="list"),
    path("<int:pk>/", views.my_client_detail, name="detail"),
]
