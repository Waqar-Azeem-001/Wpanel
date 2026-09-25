"""Customer support routes, mounted at account/support/."""
from django.urls import path

from . import views_customer as views

app_name = "support_customer"

urlpatterns = [
    path("", views.ticket_list, name="list"),
    path("new/", views.ticket_new, name="new"),
    path("<int:pk>/", views.ticket_detail, name="ticket"),
    path("<int:pk>/close/", views.ticket_close, name="close"),
    path("attachments/<int:pk>/", views.attachment, name="attachment"),
]
