from django.urls import path

from . import views, views_databases, views_email, views_files

app_name = "hosting_customer"

urlpatterns = [
    path("", views.my_hosting_list, name="list"),
    path("<int:pk>/", views.my_hosting_detail, name="detail"),
    path("<int:pk>/information/", views.my_hosting_information, name="information"),
    path("<int:pk>/addons/", views.my_hosting_addons, name="addons"),
    path("<int:pk>/manage/files/", views_files.my_hosting_files, name="files"),
    path("<int:pk>/manage/files/do/", views_files.my_hosting_file_action, name="file_action"),
    path("<int:pk>/manage/files/trash/", views_files.my_hosting_files_trash, name="files_trash"),
    path("<int:pk>/manage/files/edit/", views_files.my_hosting_file_edit, name="file_edit"),
    path("<int:pk>/manage/files/download/", views_files.my_hosting_file_download, name="file_download"),
    path("<int:pk>/manage/databases/", views_databases.my_hosting_databases, name="databases"),
    path("<int:pk>/manage/databases/browse/", views_databases.my_hosting_database_browse, name="database_browse"),
    path("<int:pk>/manage/databases/sql/", views_databases.my_hosting_database_sql, name="database_sql"),
    path("<int:pk>/manage/databases/row/", views_databases.my_hosting_database_row, name="database_row"),
    path("<int:pk>/manage/databases/do/", views_databases.my_hosting_database_action, name="database_action"),
    path("<int:pk>/manage/email/", views_email.my_hosting_email, name="email"),
    path("<int:pk>/manage/email/do/", views_email.my_hosting_email_action, name="email_action"),
    path("<int:pk>/manage/email/webmail/", views_email.my_hosting_webmail, name="webmail"),
    path("<int:pk>/manage/<slug:section>/", views.my_hosting_section, name="section"),
]
