from django.urls import path

from . import views_staff as views

app_name = "agent_staff"
urlpatterns = [
    path("", views.nodes, name="nodes"),
    path("add/", views.node_add, name="add"),
    path("<int:pk>/invite/", views.node_invite, name="invite"),
    path("<int:pk>/revoke/", views.node_revoke, name="revoke"),
    path("<int:pk>/capacity/", views.node_capacity, name="capacity"),
    path("<int:pk>/reputation/", views.node_reputation, name="reputation"),
]
