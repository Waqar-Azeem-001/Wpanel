from django.urls import path

from . import views

app_name = "clients_customer"

urlpatterns = [
    path("", views.my_clients, name="list"),
    path("contacts/", views.my_contacts, name="contacts"),
    path("contacts/<int:pk>/invite/", views.my_contacts_invite, name="contacts_invite"),
    path("<int:pk>/", views.my_client_detail, name="detail"),
]
