"""The staff console: dashboard, its widgets, global search and the audit log. Mounted at staff/."""
from django.urls import path

from . import views, views_setup, views_storage, views_users

app_name = "console"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("widgets/<slug:key>/", views.widget, name="widget"),
    path("widgets/<slug:key>/move/<str:direction>/", views.widget_move, name="widget_move"),
    path("search/", views.search, name="search"),
    path("utilities/automation/", views.automation, name="automation"),
    path("utilities/audit/", views.audit_log, name="audit_log"),
    path("utilities/audit/export/", views.audit_export, name="audit_export"),
    path("utilities/audit/purge/", views.audit_purge, name="audit_purge"),
    path("setup/storage/", views_storage.storage_page, name="storage"),
    path("setup/storage/backup-now/", views_storage.backup_now, name="storage_backup_now"),
    path("setup/storage/backups/delete/", views_storage.backups_delete, name="storage_backups_delete"),
    path("setup/storage/backups/<slug:kind>/<str:name>/download/", views_storage.backup_download, name="storage_backup_download"),
    path("setup/storage/restore/", views_storage.restore, name="storage_restore"),
    path("setup/storage/documents/delete/", views_storage.documents_delete, name="storage_documents_delete"),
    path("setup/storage/unreferenced/delete/", views_storage.unreferenced_delete, name="storage_unreferenced_delete"),
    path("users/", views_users.users, name="users"),
    path("users/<int:pk>/", views_users.user_detail, name="user_detail"),
    path("users/<int:pk>/details/", views_users.user_edit, name="user_edit"),
    path("users/<int:pk>/permissions/", views_users.user_extra_permissions, name="user_extra_permissions"),
    path("users/<int:pk>/password/", views_users.user_set_password, name="user_set_password"),
    path("users/<int:pk>/reset-link/", views_users.user_reset_link, name="user_reset_link"),
    path("users/<int:pk>/setup-link/", views_users.user_setup_link, name="user_setup_link"),
    path("users/<int:pk>/mfa-reset/", views_users.user_mfa_reset, name="user_mfa_reset"),
    path("setup/staff/", views_setup.staff_users, name="staff_users"),
    path("setup/staff/new/", views_setup.staff_user_create, name="staff_user_create"),
    path("setup/staff/<int:pk>/role/", views_setup.staff_user_role, name="staff_user_role"),
    path("setup/staff/<int:pk>/status/", views_setup.staff_user_status, name="staff_user_status"),
    path("setup/email/", views_setup.email_providers, name="email_providers"),
    path("setup/email/save/", views_setup.email_provider_save, name="email_provider_save"),
    path("setup/email/settings/", views_setup.email_settings_save, name="email_settings_save"),
    path("setup/email/<int:pk>/delete/", views_setup.email_provider_delete, name="email_provider_delete"),
    path("setup/email/bulk/", views_setup.email_provider_bulk, name="email_provider_bulk"),
    path("setup/email/<int:pk>/test/", views_setup.email_provider_test, name="email_provider_test"),
    path("setup/email/<int:pk>/check/", views_setup.email_provider_check, name="email_provider_check"),
    path("setup/registrar/", views_setup.registrars, name="registrars"),
    path("setup/registrar/save/", views_setup.registrar_save, name="registrar_save"),
    path("setup/registrar/<int:pk>/delete/", views_setup.registrar_delete, name="registrar_delete"),
    path("setup/registrar/bulk/", views_setup.registrar_bulk, name="registrar_bulk"),
]
