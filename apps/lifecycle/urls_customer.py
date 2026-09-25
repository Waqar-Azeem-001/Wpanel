"""Customer cancellation routes, mounted at account/cancellations/."""
from django.urls import path

from . import views_customer as views

app_name = "lifecycle_customer"

urlpatterns = [
    path("", views.cancellation_list, name="list"),
    path("<int:pk>/", views.cancellation_detail, name="detail"),
    path("<int:pk>/withdraw/", views.cancellation_withdraw, name="withdraw"),
    path("new/<str:kind>/<int:pk>/", views.cancellation_new, name="new"),
]
