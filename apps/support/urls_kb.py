"""The public help centre, mounted at help/."""
from django.urls import path

from . import views_kb as views

app_name = "help"

urlpatterns = [
    path("", views.index, name="index"),
    path("<slug:slug>/", views.category, name="category"),
    path("<slug:category_slug>/<slug:slug>/", views.article, name="article"),
]
