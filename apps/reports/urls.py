"""Staff report routes, mounted at staff/reports/."""
from django.urls import path

from . import views

app_name = "reports_staff"

urlpatterns = [
    path("", views.index, name="index"),
    path("<slug:slug>/", views.detail, name="report"),
]
