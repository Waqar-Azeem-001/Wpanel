"""
"Create website" for a hosting plan you already have: 1 What to create (WordPress or a custom website) -> 2 Details -> 3 Installing / ready.

A WordPress site is installed by the server with everything automatic (database, user, files, configuration); the customer gives the site's name and the administrator's name,
email and password. The password is sent once to the provider and is never stored or shown again.
"""
import re
import secrets

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.exceptions import ServiceError

from . import domain_check, services, site_apps
from .models import AppState, HostingStatus

STEPS = (("type", "Type"), ("details", "Details"), ("install", "Install"))
ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class WordPressForm(forms.Form):
    title = forms.CharField(label="Website name", max_length=100, help_text="Shown as the title of your website. You can change it later.")
    admin_user = forms.CharField(label="Administrator username", min_length=3, max_length=30)
    admin_email = forms.EmailField(label="Administrator email")
    admin_password = forms.CharField(label="Administrator password", min_length=12, max_length=128, widget=forms.TextInput(attrs={"autocomplete": "off"}),
                                     help_text="At least 12 characters. A strong one is suggested; keep it somewhere safe, it is not shown again.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def clean_title(self):
        value = self.cleaned_data["title"].strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .,&!?'()-]{0,99}", value):
            raise forms.ValidationError("Use letters, numbers and ordinary punctuation, starting with a letter or number.")
        return value

    def clean_admin_user(self):
        value = self.cleaned_data["admin_user"].strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,29}", value):
            raise forms.ValidationError("Use 3 to 30 letters, numbers, dots, dashes or underscores.")
        return value

    def clean_admin_password(self):
        value = self.cleaned_data["admin_password"]
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise forms.ValidationError("Use printable characters only.")
        return value


def _own(request, pk):
    from . import admin_access

    account = get_object_or_404(services.visible_hosting_accounts_for_user(request.user), pk=pk)
    admin_access.require(request, account)
    return account


def _suggestion():
    return "".join(secrets.choice(ALPHABET) for _ in range(20))


def _page(request, account, **extra):
    from apps.core import portal

    context = {"account": account, "sidebar": portal.service_sidebar(request, account), "steps": STEPS, "can_wordpress": site_apps.can_install_wordpress(account), **extra}
    response = render(request, "hosting/customer/setup.html", context)
    response["Cache-Control"] = "no-store"  # the page may hold a password suggestion
    return response


@login_required
def create(request):
    """The "Create website" button on the Websites page: the plans that have no website set up yet."""
    owned = services.visible_hosting_accounts_for_user(request.user).filter(client__contacts__user=request.user, status=HostingStatus.ACTIVE).distinct()
    waiting = [a for a in owned if site_apps.needs_setup(a)]
    if len(waiting) == 1:
        return redirect("hosting_customer:setup", pk=waiting[0].pk)
    return render(request, "hosting/customer/create.html", {"waiting": waiting})


@login_required
def setup(request, pk):
    account = _own(request, pk)
    existing = site_apps.current(account)
    if existing is not None and existing.state != AppState.FAILED:
        return redirect("hosting_customer:setup_status", pk=account.pk)
    if account.status != HostingStatus.ACTIVE:
        messages.error(request, "This website is not active yet, so it cannot be set up.")
        return redirect("hosting_customer:detail", pk=account.pk)
    kind = request.POST.get("type") or request.GET.get("type") or ""
    if request.method == "POST" and kind == "custom":
        try:
            site_apps.start_custom(request.user, account, request=request)
        except ServiceError as exc:
            messages.error(request, exc.message)
            return redirect("hosting_customer:setup", pk=account.pk)
        messages.success(request, "Your custom website is ready. Upload your files with the File manager or SFTP.")
        return redirect("hosting_customer:detail", pk=account.pk)
    if kind != "wordpress":
        return _page(request, account, step="type", done=[], error=existing.error if existing else "")
    if request.method == "POST":
        form = WordPressForm(request.POST)
        if form.is_valid():
            try:
                site_apps.start_wordpress(request.user, account, request=request, title=form.cleaned_data["title"], admin_user=form.cleaned_data["admin_user"],
                                          admin_email=form.cleaned_data["admin_email"], admin_password=form.cleaned_data["admin_password"])
            except ServiceError as exc:
                form.add_error(None, exc.message)
            else:
                return redirect("hosting_customer:setup_status", pk=account.pk)
    else:
        form = WordPressForm(initial={"title": account.domain, "admin_user": "siteadmin", "admin_email": request.user.email, "admin_password": _suggestion()})
    return _page(request, account, step="details", done=["type"], form=form)


@login_required
def setup_status(request, pk):
    account = _own(request, pk)
    app = site_apps.poll(request.user, account)
    if app is None:
        return redirect("hosting_customer:setup", pk=account.pk)
    extra = {}
    if app.state == AppState.READY:
        from urllib.parse import urlencode

        from django.urls import reverse

        extra = {"domain_state": domain_check.check(account), "transfer_url": reverse("domains_public:search") + "?" + urlencode({"domain": account.domain})}
    return _page(request, account, step="install", done=["type", "details"] if app.kind == "wordpress" else ["type", "details", "install"], app=app, **extra)
