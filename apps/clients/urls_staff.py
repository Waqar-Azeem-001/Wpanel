from django.urls import path

from . import views

app_name = "clients_staff"

urlpatterns = [
    path("", views.staff_client_list, name="list"),
    path("new/", views.staff_client_create, name="create"),
    path("bulk/", views.staff_client_bulk, name="bulk"),
    path("<int:pk>/", views.staff_client_detail, name="detail"),
    path("<int:pk>/edit/", views.staff_client_edit, name="edit"),
    path("<int:pk>/status/", views.staff_client_status, name="status"),
    path("<int:pk>/delete/", views.staff_client_delete, name="delete"),
    path("<int:pk>/contacts/", views.staff_contact_add, name="contact_add"),  # GET: the Contacts tab; POST: add a contact
    path("<int:pk>/tab/<slug:tab>/", views.staff_client_tab, name="tab"),
    path("<int:pk>/contacts/<int:contact_id>/role/", views.staff_contact_role, name="contact_role"),
    path("<int:pk>/contacts/<int:contact_id>/reset-link/", views.staff_contact_reset_link, name="contact_reset_link"),
    path("<int:pk>/contacts/<int:contact_id>/remove/", views.staff_contact_remove, name="contact_remove"),
]
