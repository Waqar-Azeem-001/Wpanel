"""The staff console: dashboard, its widgets, global search and the audit log. Mounted at staff/."""
from django.urls import path

from . import views

app_name = "console"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("widgets/<slug:key>/", views.widget, name="widget"),
    path("search/", views.search, name="search"),
    path("utilities/audit/", views.audit_log, name="audit_log"),
]
