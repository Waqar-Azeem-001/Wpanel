from django.urls import path

from . import views

app_name = "agent"
urlpatterns = [
    path("poll/", views.poll, name="poll"),
    path("jobs/<int:job_id>/result/", views.result, name="result"),
]
