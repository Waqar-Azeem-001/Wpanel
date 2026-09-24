"""Server-rendered account pages. All rules are delegated to ``services``."""
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from apps.core.exceptions import ServiceError

from . import forms, services


def _apply_error(form, exc, password_field=None):
    """Put a service/validation error onto the form."""
    if isinstance(exc, ValidationError):
        form.add_error(password_field, exc)
    else:
        form.add_error(None, exc.message)


def _safe_next(request, default="accounts:profile"):
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        return target
    return default


@sensitive_post_parameters("password")
def login_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:profile")
    form = forms.LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = services.authenticate_user(request=request, **form.cleaned_data)
        except ServiceError as exc:
            _apply_error(form, exc)
        else:
            login(request, user)
            return redirect(_safe_next(request))
    return render(request, "accounts/login.html", {"form": form, "next": request.GET.get("next", "")})


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "You have been signed out.")
    return redirect("accounts:login")


@sensitive_post_parameters("password", "password_confirm")
def register_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:profile")
    form = forms.RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            user = services.register_user(
                email=data["email"], password=data["password"],
                first_name=data["first_name"], last_name=data["last_name"], request=request,
            )
        except (ServiceError, ValidationError) as exc:
            _apply_error(form, exc, "password")
        else:
            login(request, user)
            messages.success(request, "Account created. Check your inbox to verify your email address.")
            return redirect("accounts:profile")
    return render(request, "accounts/register.html", {"form": form})


def verify_email_view(request, token):
    try:
        services.verify_email(token, request=request)
    except ServiceError as exc:
        return render(request, "accounts/verify_email.html", {"ok": False, "message": exc.message}, status=400)
    return render(request, "accounts/verify_email.html", {"ok": True})


@login_required
@require_POST
def resend_verification_view(request):
    services.send_verification_email(request.user)
    messages.info(request, "A new verification link has been sent if your email is unverified.")
    return redirect("accounts:profile")


def password_reset_view(request):
    form = forms.PasswordResetRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        services.request_password_reset(form.cleaned_data["email"], request=request)
        return render(request, "accounts/password_reset_sent.html")
    return render(request, "accounts/password_reset.html", {"form": form})


@sensitive_post_parameters("new_password", "new_password_confirm")
def password_reset_confirm_view(request, uidb64, token):
    if services.get_user_for_reset(uidb64, token) is None:
        return render(request, "accounts/password_reset_confirm.html", {"invalid": True}, status=400)
    form = forms.SetPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.reset_password(uidb64, token, form.cleaned_data["new_password"], request=request)
        except (ServiceError, ValidationError) as exc:
            _apply_error(form, exc, "new_password")
        else:
            messages.success(request, "Your password has been reset. You can now sign in.")
            return redirect("accounts:login")
    return render(request, "accounts/password_reset_confirm.html", {"form": form})


@login_required
def profile_view(request):
    initial = {field: getattr(request.user, field) for field in services.PROFILE_FIELDS}
    form = forms.ProfileForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        services.update_profile(request.user, request=request, **form.cleaned_data)
        messages.success(request, "Profile updated.")
        return redirect("accounts:profile")
    return render(request, "accounts/profile.html", {"form": form})


@login_required
@sensitive_post_parameters("old_password", "new_password", "new_password_confirm")
def password_change_view(request):
    form = forms.PasswordChangeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.change_password(
                request.user, old_password=form.cleaned_data["old_password"],
                new_password=form.cleaned_data["new_password"], request=request,
            )
        except (ServiceError, ValidationError) as exc:
            _apply_error(form, exc, "new_password")
        else:
            update_session_auth_hash(request, request.user)
            messages.success(request, "Password changed.")
            return redirect("accounts:profile")
    return render(request, "accounts/password_change.html", {"form": form})
