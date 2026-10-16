from django.urls import path

from . import views

app_name = "assistant"
urlpatterns = [
    path("", views.chat, name="chat"),
    path("send/", views.send, name="send"),
    path("person/", views.handoff, name="handoff"),
    path("new/", views.new_chat, name="new"),
]
