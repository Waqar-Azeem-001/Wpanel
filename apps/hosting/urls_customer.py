from django.urls import path

from . import views

app_name = "hosting_customer"

urlpatterns = [
    path("", views.my_hosting_list, name="list"),
    path("<int:pk>/", views.my_hosting_detail, name="detail"),
    path("<int:pk>/information/", views.my_hosting_information, name="information"),
    path("<int:pk>/addons/", views.my_hosting_addons, name="addons"),
]
