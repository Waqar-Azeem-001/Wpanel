from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/", views.register_view, name="register"),
    path("verify-email/resend/", views.resend_verification_view, name="resend_verification"),
    path("verify-email/<str:token>/", views.verify_email_view, name="verify_email"),
    path("password-reset/", views.password_reset_view, name="password_reset"),
    path("password-reset/<uidb64>/<token>/", views.password_reset_confirm_view, name="password_reset_confirm"),
    path("profile/", views.profile_view, name="profile"),
    path("password-change/", views.password_change_view, name="password_change"),
]
