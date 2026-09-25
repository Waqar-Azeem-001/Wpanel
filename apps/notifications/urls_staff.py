"""Staff email log and statistics, mounted at staff/notifications/."""
from django.urls import path

from . import views

app_name = "notifications_staff"

urlpatterns = [
    path("", views.email_overview, name="overview"),
    path("emails/", views.email_list, name="emails"),
    path("emails/<int:pk>/", views.email_detail, name="email"),
    path("emails/<int:pk>/resend/", views.email_resend, name="resend"),
]
