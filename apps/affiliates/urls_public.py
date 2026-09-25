"""The referral link, mounted at r/."""
from django.urls import path

from . import views_public as views

app_name = "affiliates_public"

urlpatterns = [
    path("<slug:code>/", views.referral, name="referral"),
]
