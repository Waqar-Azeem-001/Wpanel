"""The affiliate's own pages, mounted at account/affiliate/."""
from django.urls import path

from . import views_customer as views

app_name = "affiliates_customer"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("details/", views.update_details, name="details"),
    path("commissions/", views.commission_list, name="commissions"),
    path("payouts/", views.payout_list, name="payouts"),
]
