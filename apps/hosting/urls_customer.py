from django.urls import path

from . import views

app_name = "hosting_customer"

urlpatterns = [
    path("", views.my_hosting_list, name="list"),
    path("request/", views.my_hosting_request, name="request"),
    path("<int:pk>/", views.my_hosting_detail, name="detail"),
]
