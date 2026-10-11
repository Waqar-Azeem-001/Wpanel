from django.urls import path

from . import views

app_name = "migration_staff"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("retirement/", views.retirement, name="retirement"),
    path("start/", views.start, name="start"),
    path("retirement/<str:key>/", views.signoff, name="signoff"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/<str:action>/", views.act, name="act"),
]
