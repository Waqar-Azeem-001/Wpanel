from django.urls import path

from . import views_staff as views

app_name = "agent_staff"
urlpatterns = [
    path("", views.nodes, name="nodes"),
    path("add/", views.node_add, name="add"),
    path("backups/", views.backups, name="backups"),
    path("monitoring/", views.monitoring_page, name="monitoring"),
    path("<int:pk>/backup-save/", views.backup_save, name="backup_save"),
    path("<int:pk>/backup-apply/", views.backup_apply, name="backup_apply"),
    path("<int:pk>/backup-config/", views.backup_node_config, name="backup_node_config"),
    path("<int:pk>/invite/", views.node_invite, name="invite"),
    path("<int:pk>/revoke/", views.node_revoke, name="revoke"),
    path("<int:pk>/capacity/", views.node_capacity, name="capacity"),
    path("<int:pk>/reputation/", views.node_reputation, name="reputation"),
    path("<int:pk>/isolation/", views.node_isolation, name="isolation"),
]
