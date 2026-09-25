from django.urls import path

from . import views

app_name = "orders_staff"

urlpatterns = [
    path("", views.staff_order_list, name="list"),
    path("new/", views.staff_order_new, name="new"),
    path("<int:pk>/", views.staff_order_detail, name="detail"),
    path("<int:pk>/cancel/", views.staff_order_cancel, name="cancel"),
    path("<int:pk>/fraud/", views.staff_order_fraud, name="fraud"),
    path("<int:pk>/clear-fraud/", views.staff_order_clear_fraud, name="clear_fraud"),
    path("<int:pk>/retry/", views.staff_order_retry, name="retry"),
    path("<int:pk>/suspend/", views.staff_order_suspend, name="suspend"),
    path("<int:pk>/unsuspend/", views.staff_order_unsuspend, name="unsuspend"),
    path("<int:pk>/terminate/", views.staff_order_terminate, name="terminate"),
]
