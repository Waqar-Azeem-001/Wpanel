"""Staff affiliate routes, mounted at staff/affiliates/."""
from django.urls import path

from . import views_staff as views

app_name = "affiliates_staff"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("list/", views.affiliate_list, name="affiliates"),
    path("settings/", views.settings_page, name="settings"),
    path("commissions/", views.commission_list, name="commissions"),
    path("commissions/<int:pk>/approve/", views.commission_approve, name="commission_approve"),
    path("commissions/<int:pk>/reject/", views.commission_reject, name="commission_reject"),
    path("payouts/", views.payout_list, name="payouts"),
    path("attribute/<int:client_pk>/", views.attribute, name="attribute"),
    path("<int:pk>/", views.affiliate_detail, name="affiliate"),
    path("<int:pk>/approve/", views.approve, name="approve"),
    path("<int:pk>/reject/", views.reject, name="reject"),
    path("<int:pk>/suspend/", views.suspend, name="suspend"),
    path("<int:pk>/reactivate/", views.reactivate, name="reactivate"),
    path("<int:pk>/rule/", views.override, name="rule"),
    path("<int:pk>/code/", views.change_code, name="code"),
    path("<int:pk>/details/", views.payout_details, name="details"),
    path("<int:pk>/payout/", views.record_payout, name="payout"),
]
