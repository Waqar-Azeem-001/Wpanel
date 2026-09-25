"""Staff renewal routes, mounted at staff/renewals/."""
from django.urls import path

from . import views

app_name = "renewals_staff"

urlpatterns = [
    path("", views.staff_list, name="list"),
    path("generate/", views.staff_generate, name="generate"),
    path("hosting/<int:pk>/term/", views.staff_hosting_term, name="hosting_term"),
    path("hosting/<int:pk>/renew/", views.staff_hosting_renew, name="hosting_renew"),
    path("domains/<int:pk>/renew/", views.staff_domain_renew, name="domain_renew"),
    path("changes/<int:pk>/retry/", views.staff_retry, name="retry"),
    path("changes/<int:pk>/dismiss/", views.staff_dismiss, name="dismiss"),
]
