from django.urls import path

from . import views

app_name = "clients_staff"

urlpatterns = [
    path("", views.staff_client_list, name="list"),
    path("new/", views.staff_client_create, name="create"),
    path("<int:pk>/", views.staff_client_detail, name="detail"),
    path("<int:pk>/edit/", views.staff_client_edit, name="edit"),
    path("<int:pk>/status/", views.staff_client_status, name="status"),
    path("<int:pk>/contacts/", views.staff_contact_add, name="contact_add"),
    path("<int:pk>/contacts/<int:contact_id>/role/", views.staff_contact_role, name="contact_role"),
    path("<int:pk>/contacts/<int:contact_id>/remove/", views.staff_contact_remove, name="contact_remove"),
]
