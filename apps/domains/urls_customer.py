from django.urls import path

from . import views

app_name = "domains_customer"

urlpatterns = [
    path("", views.my_domain_list, name="list"),
    path("<int:pk>/", views.my_domain_detail, name="detail"),
    path("<int:pk>/auto-renew/", views.my_domain_auto_renew, name="auto_renew"),
    path("<int:pk>/nameservers/", views.my_domain_nameservers, name="nameservers"),
    path("<int:pk>/lock/", views.my_domain_lock, {"locked": True}, name="lock"),
    path("<int:pk>/unlock/", views.my_domain_lock, {"locked": False}, name="unlock"),
    path("<int:pk>/dns/", views.my_domain_dns_add, name="dns_add"),
    path("<int:pk>/dns/<int:record_id>/remove/", views.my_domain_dns_remove, name="dns_remove"),
]
