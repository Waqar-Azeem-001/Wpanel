"""Staff email log and statistics, mounted at staff/notifications/."""
from django.urls import path

from . import views

app_name = "notifications_staff"

urlpatterns = [
    path("", views.email_overview, name="overview"),
    path("emails/", views.email_list, name="emails"),
    path("emails/<int:pk>/", views.email_detail, name="email"),
    path("emails/<int:pk>/resend/", views.email_resend, name="resend"),
    path("emails/<int:pk>/delete/", views.email_delete, name="delete"),
    path("emails/bulk/", views.email_bulk, name="bulk"),
    path("emails/retry-failed/", views.email_retry_failed, name="retry_failed"),
    path("emails/purge/", views.email_purge, name="purge"),
]
