from django.urls import path

from . import views

app_name = "agent"
urlpatterns = [
    path("enroll/", views.enroll, name="enroll"),
    path("renew/", views.renew, name="renew"),
    path("heartbeat/", views.heartbeat, name="heartbeat"),
    path("poll/", views.poll, name="poll"),
    path("jobs/<int:job_id>/result/", views.result, name="result"),
]
