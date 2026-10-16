from django.urls import path

from . import views_chats

app_name = "livechat"
urlpatterns = [
    path("", views_chats.chat_list, name="list"),
    path("availability/", views_chats.availability, name="availability"),
    path("callbacks/", views_chats.callbacks, name="callbacks"),
    path("callbacks/<int:pk>/done/", views_chats.callback_done, name="callback_done"),
    path("<int:pk>/", views_chats.chat_detail, name="detail"),
    path("<int:pk>/poll/", views_chats.chat_poll, name="poll"),
    path("<int:pk>/do/", views_chats.chat_action, name="action"),
]
