"""Customer renewal and upgrade routes, mounted at account/renewals/."""
from django.urls import path

from . import views

app_name = "renewals_customer"

urlpatterns = [
    path("hosting/<int:pk>/renew/", views.hosting_renew, name="hosting_renew"),
    path("hosting/<int:pk>/upgrade/", views.hosting_upgrade, name="hosting_upgrade"),
    path("domains/<int:pk>/renew/", views.domain_renew, name="domain_renew"),
]
