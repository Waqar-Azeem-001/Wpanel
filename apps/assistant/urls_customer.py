from django.urls import path

from . import views

app_name = "assistant"
urlpatterns = [
    path("", views.chat, name="chat"),
    path("panel/", views.panel, name="panel"),
    path("send/", views.send, name="send"),
    path("person/", views.handoff, name="handoff"),
    path("poll/", views.poll, name="poll"),
    path("callback/", views.callback, name="callback"),
    path("feedback/<int:pk>/<str:vote>/", views.feedback, name="feedback"),
    path("actions/<int:pk>/<str:verb>/", views.act, name="act"),
    path("verify/<int:pk>/", views.verify, name="verify"),
    path("verify/<int:pk>/resend/", views.verify_resend, name="verify_resend"),
    path("ticket/", views.ticket_instead, name="ticket"),
    path("new/", views.new_chat, name="new"),
]
