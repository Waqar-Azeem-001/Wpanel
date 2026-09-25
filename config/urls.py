from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path

from apps.clients.views_dashboard import dashboard
from apps.core.redirects import retired_urlpatterns
from apps.notifications import views as notification_views
from apps.products.views import public_home


def home(request):
    """The site's front door: visitors see the storefront, customers their dashboard, staff their dashboard."""
    if not request.user.is_authenticated:
        return public_home(request)
    if not request.user.is_staff and request.user.client_contacts.exists():
        return redirect("dashboard")
    if request.user.is_staff:
        return redirect("console:dashboard")
    return redirect("accounts:profile")


urlpatterns = [
    path("", home, name="home"),
    path("account/", dashboard, name="dashboard"),
    path("staff/", include("apps.console.urls")),
    *retired_urlpatterns(),
    path("admin/", admin.site.urls),
    path("api/v1/", include(("config.api_urls", "v1"), namespace="v1")),
    path("account/", include("apps.accounts.urls")),
    path("account/client/", include("apps.clients.urls_customer")),
    path("staff/clients/", include("apps.clients.urls_staff")),
    path("staff/", include("apps.products.urls_staff")),
    path("products/", include("apps.products.urls_public")),
    path("account/domains/", include("apps.domains.urls_customer")),
    path("staff/domains/", include("apps.domains.urls_staff")),
    path("domains/", include("apps.domains.urls_public")),
    path("account/hosting/", include("apps.hosting.urls_customer")),
    path("staff/hosting/", include("apps.hosting.urls_staff")),
    path("staff/orders/", include("apps.orders.urls_staff")),
    path("staff/billing/", include("apps.billing.urls_staff")),
    path("account/billing/", include("apps.billing.urls_customer")),
    path("account/renewals/", include("apps.renewals.urls_customer")),
    path("account/cancellations/", include("apps.lifecycle.urls_customer")),
    path("account/affiliate/", include("apps.affiliates.urls_customer")),
    path("staff/affiliates/", include("apps.affiliates.urls_staff")),
    path("staff/reports/", include("apps.reports.urls")),
    path("staff/settings/brand/", include("apps.branding.urls_staff")),
    path("", include("apps.branding.urls")),
    path("r/", include("apps.affiliates.urls_public")),
    path("staff/lifecycle/", include("apps.lifecycle.urls_staff")),
    path("account/support/", include("apps.support.urls_customer")),
    path("account/notifications/", include("apps.notifications.urls")),
    path("staff/notifications/", include("apps.notifications.urls_staff")),
    path("e/o/<uuid:token>.gif", notification_views.open_pixel, name="email_open_pixel"),
    path("staff/support/", include("apps.support.urls_staff")),
    path("help/", include("apps.support.urls_kb")),
    path("staff/renewals/", include("apps.renewals.urls_staff")),
    path("", include("apps.orders.urls_customer")),
]
