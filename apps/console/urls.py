"""The staff console: dashboard, its widgets, global search and the audit log. Mounted at staff/."""
from django.urls import path

from . import views, views_setup

app_name = "console"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("widgets/<slug:key>/", views.widget, name="widget"),
    path("search/", views.search, name="search"),
    path("utilities/audit/", views.audit_log, name="audit_log"),
    path("setup/staff/", views_setup.staff_users, name="staff_users"),
    path("setup/staff/new/", views_setup.staff_user_create, name="staff_user_create"),
    path("setup/staff/<int:pk>/role/", views_setup.staff_user_role, name="staff_user_role"),
    path("setup/staff/<int:pk>/status/", views_setup.staff_user_status, name="staff_user_status"),
    path("setup/email/", views_setup.email_providers, name="email_providers"),
    path("setup/email/save/", views_setup.email_provider_save, name="email_provider_save"),
    path("setup/email/<int:pk>/delete/", views_setup.email_provider_delete, name="email_provider_delete"),
    path("setup/email/<int:pk>/test/", views_setup.email_provider_test, name="email_provider_test"),
    path("setup/registrar/", views_setup.registrars, name="registrars"),
    path("setup/registrar/save/", views_setup.registrar_save, name="registrar_save"),
    path("setup/registrar/<int:pk>/delete/", views_setup.registrar_delete, name="registrar_delete"),
]
