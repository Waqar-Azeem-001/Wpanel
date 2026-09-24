from django.urls import path

from . import views

app_name = "domains_staff"

urlpatterns = [
    path("", views.staff_domain_list, name="list"),
    path("tlds/", views.staff_tld_list, name="tld_list"),
    path("tlds/save/", views.staff_tld_save, name="tld_save"),
    path("tlds/<int:pk>/status/", views.staff_tld_status, name="tld_status"),
    path("<int:pk>/", views.staff_domain_detail, name="detail"),
    path("<int:pk>/complete/", views.staff_domain_complete, name="complete"),
    path("<int:pk>/cancel/", views.staff_domain_cancel, name="cancel"),
    path("<int:pk>/renew/", views.staff_domain_renew, name="renew"),
    path("<int:pk>/sync/", views.staff_domain_sync, name="sync"),
    path("<int:pk>/nameservers/", views.staff_domain_nameservers, name="nameservers"),
    path("<int:pk>/lock/", views.staff_domain_lock, {"locked": True}, name="lock"),
    path("<int:pk>/unlock/", views.staff_domain_lock, {"locked": False}, name="unlock"),
    path("<int:pk>/dns/", views.staff_domain_dns_add, name="dns_add"),
    path("<int:pk>/dns/<int:record_id>/remove/", views.staff_domain_dns_remove, name="dns_remove"),
]
