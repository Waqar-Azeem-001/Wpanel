from django.urls import path

from . import views

app_name = "hosting_staff"

urlpatterns = [
    path("", views.staff_hosting_list, name="list"),
    path("bulk/", views.staff_hosting_bulk, name="bulk"),
    path("<int:pk>/", views.staff_hosting_detail, name="detail"),
    path("<int:pk>/admin-access/", views.staff_hosting_admin_access_start, name="admin_access"),
    path("<int:pk>/admin-access/end/", views.staff_hosting_admin_access_end, name="admin_access_end"),
    path("<int:pk>/assign-server/", views.staff_hosting_assign_server, name="assign_server"),
    path("<int:pk>/complete/", views.staff_hosting_complete, name="complete"),
    path("<int:pk>/cancel/", views.staff_hosting_cancel, name="cancel"),
    path("<int:pk>/suspend/", views.staff_hosting_suspend, name="suspend"),
    path("<int:pk>/unsuspend/", views.staff_hosting_unsuspend, name="unsuspend"),
    path("<int:pk>/terminate/", views.staff_hosting_terminate, name="terminate"),
    path("<int:pk>/change-package/", views.staff_hosting_change_package, name="change_package"),
    path("<int:pk>/sync-status/", views.staff_hosting_sync_status, name="sync_status"),
    path("<int:pk>/sync-usage/", views.staff_hosting_sync_usage, name="sync_usage"),
    path("<int:pk>/backup/", views.staff_hosting_backup, name="backup"),
]
