"""Staff support routes, mounted at staff/support/."""
from django.urls import path

from . import views_staff as views

app_name = "support_staff"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("tickets/", views.ticket_list, name="tickets"),
    path("tickets/new/", views.ticket_new, name="ticket_new"),
    path("tickets/<int:pk>/", views.ticket_detail, name="ticket"),
    path("tickets/<int:pk>/assign/", views.ticket_assign, name="assign"),
    path("tickets/<int:pk>/take/", views.ticket_take, name="take"),
    path("tickets/<int:pk>/priority/", views.ticket_priority, name="priority"),
    path("tickets/<int:pk>/department/", views.ticket_department, name="department"),
    path("tickets/<int:pk>/status/", views.ticket_status, name="status"),
    path("departments/", views.departments, name="departments"),
    path("departments/save/", views.department_save, name="department_save"),
    path("replies/", views.replies, name="replies"),
    path("replies/save/", views.reply_save, name="reply_save"),
    path("kb/", views.kb_manage, name="kb"),
    path("kb/categories/save/", views.kb_category_save, name="kb_category_save"),
    path("kb/articles/save/", views.kb_article_save, name="kb_article_save"),
    path("kb/articles/<int:pk>/delete/", views.kb_article_delete, name="kb_article_delete"),
]
