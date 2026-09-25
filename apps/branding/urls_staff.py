"""Staff brand settings, mounted at staff/settings/brand/."""
from django.urls import path

from . import views

app_name = "brand_staff"

urlpatterns = [
    path("", views.settings_page, name="settings"),
]
