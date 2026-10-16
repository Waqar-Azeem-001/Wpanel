from django.urls import path

from . import views, views_knowledge

app_name = "assistant_staff"
urlpatterns = [
    path("", views.staff_settings, name="settings"),
    path("knowledge/", views_knowledge.knowledge, name="knowledge"),
    path("knowledge/new/", views_knowledge.entry_form, name="knowledge_new"),
    path("knowledge/<int:pk>/", views_knowledge.entry_form, name="knowledge_edit"),
    path("knowledge/<int:pk>/delete/", views_knowledge.entry_delete, name="knowledge_delete"),
    path("knowledge/gaps/<int:pk>/dismiss/", views_knowledge.gap_dismiss, name="gap_dismiss"),
    path("<int:pk>/", views.staff_chat, name="chat"),
]
