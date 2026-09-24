from django.urls import path

from . import views

app_name = "domains_public"

urlpatterns = [
    path("", views.domain_search, name="search"),
]
