from django.urls import path

from . import views

app_name = "assistant_staff"
urlpatterns = [
    path("", views.staff_settings, name="settings"),
    path("<int:pk>/", views.staff_chat, name="chat"),
]
