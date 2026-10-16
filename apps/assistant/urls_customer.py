from django.urls import path

from . import views

app_name = "assistant"
urlpatterns = [
    path("", views.chat, name="chat"),
    path("panel/", views.panel, name="panel"),
    path("send/", views.send, name="send"),
    path("person/", views.handoff, name="handoff"),
    path("poll/", views.poll, name="poll"),
    path("ticket/", views.ticket_instead, name="ticket"),
    path("new/", views.new_chat, name="new"),
]
