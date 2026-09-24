from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="accounts:profile", permanent=False)),
    path("admin/", admin.site.urls),
    path("api/v1/", include(("config.api_urls", "v1"), namespace="v1")),
    path("account/", include("apps.accounts.urls")),
    path("account/client/", include("apps.clients.urls_customer")),
    path("staff/clients/", include("apps.clients.urls_staff")),
    path("staff/", include("apps.products.urls_staff")),
    path("products/", include("apps.products.urls_public")),
]
