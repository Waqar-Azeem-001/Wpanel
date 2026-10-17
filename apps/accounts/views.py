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

from . import devices, forms, mfa, services


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


REMEMBER_ME_SECONDS = 60 * 60 * 24 * 30


def _finish_login(request, user, *, remember, next_url):
    login(request, user)
    # Ticked: stay signed in for 30 days. Not ticked: until the browser is closed (and never longer than the session age).
    request.session.set_expiry(REMEMBER_ME_SECONDS if remember else 0)
    return devices.register_login(request, redirect(next_url), user)


@sensitive_post_parameters("password")
def login_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:profile")
    form = forms.LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        remember = form.cleaned_data.pop("remember", False)
        try:
            user = services.authenticate_user(request=request, **form.cleaned_data)
        except ServiceError as exc:
            _apply_error(form, exc)
        else:
            if devices.needs_code(request, user):  # a new device: an emailed code first, then (if on) the authenticator code
                devices.start_pending(request, user, remember=remember, next_url=_safe_next(request))
                try:
                    devices.send_code(user, request)
                except ServiceError as exc:
                    messages.error(request, exc.message)
                return redirect("accounts:signin_code")
            if user.mfa_enabled:
                services.start_mfa_challenge(request, user, remember=remember, next_url=_safe_next(request))
                return redirect("accounts:mfa_verify")
            return _finish_login(request, user, remember=remember, next_url=_safe_next(request))
    from apps.support import kb

    return render(request, "accounts/login.html", {"form": form, "next": request.GET.get("next", ""),
                                                   "help_articles": list(kb.public_articles()[:3])})


@sensitive_post_parameters("code")
def signin_code_view(request):
    """The step for a new device: the six-digit code that was emailed. Reached only mid sign-in; nobody is signed in until it is right."""
    user = devices.pending_user(request)
    if user is None:
        messages.info(request, "That sign-in has expired. Please sign in again.")
        return redirect("accounts:login")
    form = forms.SignInCodeForm(request.POST or None)
    if request.method == "POST" and request.POST.get("resend"):
        try:
            devices.send_code(user, request, resend=True)
            messages.success(request, "A new code is on its way.")
        except ServiceError as exc:
            messages.error(request, exc.message)
        return redirect("accounts:signin_code")
    if request.method == "POST" and form.is_valid():
        try:
            ok = devices.verify_code(user, form.cleaned_data["code"], request)
        except ServiceError as exc:
            form.add_error(None, exc.message)
        else:
            if ok:
                remember = request.session.get(devices.PENDING_REMEMBER, False)
                next_url = request.session.get(devices.PENDING_NEXT) or "accounts:profile"
                devices.clear_pending(request)
                if user.mfa_enabled:
                    services.start_mfa_challenge(request, user, remember=remember, next_url=next_url)
                    return redirect("accounts:mfa_verify")
                return _finish_login(request, user, remember=remember, next_url=next_url)
            form.add_error("code", "That code is not right, or it has expired.")
    return render(request, "accounts/signin_code.html", {"form": form, "email": user.email, "minutes": devices.CODE_MINUTES})


@sensitive_post_parameters("code")
def mfa_verify_view(request):
    """The second step of signing in: a code from the authenticator app, or a recovery code. Reached only mid sign-in."""
    user = services.get_pending_mfa_user(request)
    if user is None:
        messages.info(request, "That sign-in has expired. Please sign in again.")
        return redirect("accounts:login")
    form = forms.MFACodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            ok = services.mfa_verify_code_or_recovery(user, form.cleaned_data["code"])
        except ServiceError as exc:
            form.add_error(None, exc.message)
        else:
            if ok:
                remember = request.session.get(services.MFA_PENDING_REMEMBER_KEY, False)
                next_url = request.session.get(services.MFA_PENDING_NEXT_KEY) or "accounts:profile"
                services.clear_mfa_pending(request)
                return _finish_login(request, user, remember=remember, next_url=next_url)
            form.add_error("code", "That code is not right.")
    return render(request, "accounts/mfa_verify.html", {"form": form, "email": user.email})


@login_required
def mfa_setup_view(request):
    """Turn two-factor sign-in on: shows the secret to add to an authenticator app, then confirms it with a code."""
    if request.user.mfa_enabled:
        return render(request, "accounts/mfa_setup.html", {
            "enabled": True, "confirm_form": forms.MFACodeForm(), "password_form": forms.MFAPasswordConfirmForm(),
            "recovery_remaining": request.user.mfa_recovery_codes.filter(used_at__isnull=True).count()})
    form = forms.MFACodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            codes = services.mfa_confirm_setup(request.user, form.cleaned_data["code"], request=request)
        except ServiceError as exc:
            form.add_error("code", exc.message)
        else:
            request.session["mfa_new_recovery_codes"] = codes
            return redirect("accounts:mfa_recovery_codes")
    secret, uri = services.mfa_begin_setup(request.user)
    return render(request, "accounts/mfa_setup.html", {
        "enabled": False, "confirm_form": form, "secret": secret, "secret_formatted": mfa.formatted(secret),
        "otpauth_uri": uri})


@login_required
def mfa_recovery_codes_view(request):
    """Shown exactly once, right after setup (or after regenerating): the ten codes are removed from the session as they are shown."""
    codes = request.session.pop("mfa_new_recovery_codes", None)
    if not codes:
        return redirect("accounts:mfa_setup")
    return render(request, "accounts/mfa_recovery_codes.html", {"codes": codes})


@require_POST
@login_required
def mfa_disable_view(request):
    form = forms.MFAPasswordConfirmForm(request.POST)
    if form.is_valid():
        try:
            services.mfa_disable(request.user, password=form.cleaned_data["password"], request=request)
        except ServiceError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "Two-factor sign-in is now off.")
            return redirect("accounts:mfa_setup")
    messages.error(request, "Enter your current password to turn two-factor sign-in off.")
    return redirect("accounts:mfa_setup")


@require_POST
@login_required
def mfa_recovery_regenerate_view(request):
    form = forms.MFAPasswordConfirmForm(request.POST)
    if form.is_valid():
        try:
            codes = services.mfa_regenerate_recovery_codes(request.user, password=form.cleaned_data["password"], request=request)
        except ServiceError as exc:
            messages.error(request, exc.message)
        else:
            request.session["mfa_new_recovery_codes"] = codes
            return redirect("accounts:mfa_recovery_codes")
    else:
        messages.error(request, "Enter your current password to make new recovery codes.")
    return redirect("accounts:mfa_setup")


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
                first_name=data["first_name"], last_name=data["last_name"],
                company_name=data["company_name"], referral_code=request.COOKIES.get("wp_ref", ""),
                request=request,
            )
        except (ServiceError, ValidationError) as exc:
            _apply_error(form, exc, "password")
        else:
            login(request, user)
            devices.register_login(request, redirect("accounts:profile"), user)  # the first device
            messages.success(request, "Account created. Check your inbox to verify your email address.")
            response = devices.register_login(request, redirect("accounts:profile"), user)
            response.delete_cookie("wp_ref")  # the referral, if any, is now recorded on the account
            return response
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
