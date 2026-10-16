from django.urls import path

from . import views_chats

app_name = "livechat"
urlpatterns = [
    path("", views_chats.chat_list, name="list"),
    path("<int:pk>/", views_chats.chat_detail, name="detail"),
    path("<int:pk>/poll/", views_chats.chat_poll, name="poll"),
    path("<int:pk>/do/", views_chats.chat_action, name="action"),
]
