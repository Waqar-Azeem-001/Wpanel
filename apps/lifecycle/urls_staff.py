"""Staff lifecycle and cancellation routes, mounted at staff/lifecycle/."""
from django.urls import path

from . import views_staff as views

app_name = "lifecycle_staff"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("settings/", views.settings_page, name="settings"),
    path("cancellations/", views.cancellation_list, name="cancellations"),
    path("cancellations/<int:pk>/", views.cancellation_detail, name="cancellation"),
    path("cancellations/<int:pk>/approve/", views.cancellation_approve, name="approve"),
    path("cancellations/<int:pk>/reject/", views.cancellation_reject, name="reject"),
    path("cancellations/<int:pk>/retry/", views.cancellation_retry, name="retry"),
    path("cancellations/<int:pk>/withdraw/", views.cancellation_withdraw, name="withdraw"),
    path("cancel/<str:kind>/<int:pk>/", views.cancel_service, name="cancel_service"),
]
