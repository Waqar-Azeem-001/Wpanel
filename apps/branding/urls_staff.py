"""Staff brand settings, mounted at staff/settings/brand/."""
from django.urls import path

from . import views, views_pages

app_name = "brand_staff"

urlpatterns = [
    path("", views.settings_page, name="settings"),
    path("menu/", views.menu_page, name="menu"),
    path("home/", views.home_page, name="home"),
    path("pages/", views_pages.staff_pages, name="pages"),
    path("pages/new/", views_pages.staff_page_edit, name="page_new"),
    path("pages/<int:pk>/", views_pages.staff_page_edit, name="page"),
    path("pages/<int:pk>/delete/", views_pages.staff_page_delete, name="page_delete"),
]
