"""Signed-in notification pages, mounted at account/notifications/."""
from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("mark-read/", views.mark_all_read, name="mark_all_read"),
    path("preferences/", views.preferences, name="preferences"),
    path("<int:pk>/open/", views.open_notification, name="open"),
]
