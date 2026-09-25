"""The brand's public assets: the token stylesheet, the logo and the favicon."""
from django.urls import path

from . import views

urlpatterns = [
    path("brand.css", views.brand_css, name="brand_css"),
    path("brand/logo/", views.brand_logo, name="brand_logo"),
    path("brand/favicon/", views.brand_favicon, name="brand_favicon"),
]
