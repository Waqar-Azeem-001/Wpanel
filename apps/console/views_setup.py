"""Setup screens that used to need the Django admin: staff and roles, the email provider, the registrar."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts import services as account_services
from apps.accounts.models import User
from apps.accounts.roles import STAFF_ROLES, perm
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError
from apps.core.web import ACTION_ERRORS, apply_form_error, error_text
from apps.domains import services as domain_services
from apps.domains.models import RegistrarProvider
from apps.notifications import services as notification_services
from apps.notifications.models import EmailProvider

from . import forms, views_users

VIEW_USERS, VIEW_PROVIDERS = perm("view_users"), perm("view_providers")


def _flash(request, action):
    """Run ``action`` (returns a message or None); show the result; True if it worked."""
    try:
        message = action()
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
        return False
    if message:
        messages.success(request, message)
    return True


# --- Staff and roles ---------------------------------------------------------------------------------------------------

_allowed_roles = views_users.allowed_roles


@portal_permission_required(VIEW_USERS)
def staff_users(request):
    """The old Staff & Roles page: staff are now the Users screen (the address is kept for old links and bookmarks)."""
    return redirect("console:users")


@require_POST
@portal_permission_required(perm("manage_users"), perm("assign_roles"))
def staff_user_create(request):
    form = forms.StaffUserForm(request.POST, allowed_roles=_allowed_roles(request.user))
    if form.is_valid():
        try:
            user = account_services.create_staff_user(request.user, request=request, **form.cleaned_data)
        except (ServiceError, ValidationError) as exc:
            apply_form_error(form, exc)
        else:
            messages.success(request, f"{user.email} added as {user.get_role_display()}. A link to set their password was emailed.")
            return redirect("console:user_detail", pk=user.pk)
    return views_users.users(request, create_form=form)


def _user_action(request, pk, form_class, call, done, *, staff_only=False):
    user = get_object_or_404(User, pk=pk, **({"role__in": STAFF_ROLES} if staff_only else {}))
    form = form_class(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a valid value.")
    else:
        _flash(request, lambda: (call(user, form.cleaned_data), done(user, form.cleaned_data))[1])
    return redirect("console:user_detail", pk=user.pk)


@require_POST
@portal_permission_required(perm("manage_users"), perm("assign_roles"))
def staff_user_role(request, pk):
    return _user_action(request, pk, forms.StaffRoleForm,
                        lambda u, d: account_services.assign_role(request.user, u, d["role"], request=request),
                        lambda u, d: f"{u.email} is now {u.get_role_display()}.", staff_only=True)


@require_POST
@portal_permission_required(perm("manage_users"))
def staff_user_status(request, pk):
    return _user_action(request, pk, forms.StaffStatusForm,
                        lambda u, d: account_services.set_account_status(request.user, u, d["status"], reason=d["reason"],
                                                                         request=request),
                        lambda u, d: f"{u.email} is now {u.get_status_display().lower()}.")


# --- Email provider ----------------------------------------------------------------------------------------------------

def _provider_initial(provider):
    return {f: getattr(provider, f) for f in ("name", "host", "port", "username", "use_tls", "use_ssl", "timeout",
                                                "from_email", "is_active")}


@portal_permission_required(VIEW_PROVIDERS)
def email_providers(request, form=None, editing=None):
    editing = editing or EmailProvider.objects.filter(pk=request.GET.get("edit") or 0).first()
    return render(request, "console/email_providers.html", {
        "providers": EmailProvider.objects.order_by("-is_active", "name"), "editing": editing,
        "form": form or forms.EmailProviderForm(initial=_provider_initial(editing) if editing else None),
        "test_form": forms.TestEmailForm(), "can_manage": request.user.has_perm(perm("manage_providers"))})


@require_POST
@portal_permission_required(perm("manage_providers"))
def email_provider_save(request):
    editing = EmailProvider.objects.filter(pk=request.POST.get("id") or 0).first()
    form = forms.EmailProviderForm(request.POST)
    if form.is_valid():
        data = dict(form.cleaned_data)
        password = data.pop("password")
        try:
            provider = notification_services.save_email_provider(request.user, editing, data, password=password, request=request)
        except (ServiceError, ValidationError) as exc:
            apply_form_error(form, exc)
        else:
            messages.success(request, f"{provider.name} saved{' and now in use' if provider.is_active else ''}.")
            return redirect("console:email_providers")
    return email_providers(request, form=form, editing=editing)


@require_POST
@portal_permission_required(perm("manage_providers"))
def email_provider_delete(request, pk):
    provider = get_object_or_404(EmailProvider, pk=pk)
    _flash(request, lambda: (notification_services.delete_email_provider(request.user, provider, request=request),
                             f"{provider.name} deleted.")[1])
    return redirect("console:email_providers")


@require_POST
@portal_permission_required(perm("manage_providers"))
def email_provider_test(request, pk):
    provider = get_object_or_404(EmailProvider, pk=pk)
    form = forms.TestEmailForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a valid email address to send the test to.")
    else:
        try:
            ok, detail = notification_services.send_test_email(request.user, provider, form.cleaned_data["to_email"],
                                                               request=request)
        except ACTION_ERRORS as exc:
            messages.error(request, error_text(exc))
        else:
            (messages.success if ok else messages.error)(
                request, f"Test email to {form.cleaned_data['to_email']}: {detail}")
    return redirect("console:email_providers")


# --- Registrar ---------------------------------------------------------------------------------------------------------

def _registrar_initial(provider):
    return {f: getattr(provider, f) for f in ("name", "kind", "sandbox", "is_active")}


@portal_permission_required(VIEW_PROVIDERS)
def registrars(request, form=None, editing=None):
    editing = editing or RegistrarProvider.objects.filter(pk=request.GET.get("edit") or 0).first()
    return render(request, "console/registrars.html", {
        "providers": RegistrarProvider.objects.order_by("-is_active", "name"), "editing": editing,
        "form": form or forms.RegistrarForm(initial=_registrar_initial(editing) if editing else None),
        "can_manage": request.user.has_perm(perm("manage_providers"))})


@require_POST
@portal_permission_required(perm("manage_providers"))
def registrar_save(request):
    editing = RegistrarProvider.objects.filter(pk=request.POST.get("id") or 0).first()
    form = forms.RegistrarForm(request.POST)
    if form.is_valid():
        data = dict(form.cleaned_data)
        credentials = data.pop("credentials")
        try:
            provider = domain_services.save_registrar_provider(request.user, editing, data, credentials=credentials,
                                                               request=request)
        except (ServiceError, ValidationError) as exc:
            apply_form_error(form, exc)
        else:
            messages.success(request, f"{provider.name} saved{' and now in use' if provider.is_active else ''}.")
            return redirect("console:registrars")
    return registrars(request, form=form, editing=editing)


@require_POST
@portal_permission_required(perm("manage_providers"))
def registrar_delete(request, pk):
    provider = get_object_or_404(RegistrarProvider, pk=pk)
    _flash(request, lambda: (domain_services.delete_registrar_provider(request.user, provider, request=request),
                             f"{provider.name} deleted.")[1])
    return redirect("console:registrars")
