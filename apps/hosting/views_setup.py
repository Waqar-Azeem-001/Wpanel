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
from django.http import Http404
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


def _urls(account, domain=""):
    """Where the steps of one website lead: the plan's own website, or one added to the plan (then the plan's Websites page is home)."""
    from django.urls import reverse

    if domain:
        return {"setup_url": reverse("hosting_customer:site_setup", args=[account.pk, domain]), "status_url": reverse("hosting_customer:site_setup_status", args=[account.pk, domain]),
                "done_url": reverse("hosting_customer:plan", args=[account.pk]), "site_domain": domain, "visit_url": site_apps.site_url(account, domain)}
    return {"setup_url": reverse("hosting_customer:setup", args=[account.pk]), "status_url": reverse("hosting_customer:setup_status", args=[account.pk]),
            "done_url": reverse("hosting_customer:detail", args=[account.pk]), "site_domain": account.domain, "visit_url": account.site_url}


def _page(request, account, domain="", **extra):
    from apps.core import portal

    context = {"account": account, "sidebar": portal.service_sidebar(request, account), "steps": STEPS, "can_wordpress": site_apps.can_install_wordpress(account),
               **_urls(account, domain), **extra}
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


def _site_redirect(name, account, domain):
    if domain:
        return redirect("hosting_customer:site_" + name, pk=account.pk, domain=domain)
    return redirect("hosting_customer:" + name, pk=account.pk)


def _owns_domain(request, account, domain):
    """A domain added to this plan: one of its websites, or one the plan's server lists for it."""
    from . import plan_sites
    from .models import WebsiteApp

    if WebsiteApp.objects.filter(account=account, domain=domain).exists():
        return True
    return domain in {s["domain"] for s in plan_sites.summary(request.user, account)["sites"]}


@login_required
def setup(request, pk, domain=""):
    account = _own(request, pk)
    domain = "" if (domain or account.domain).lower() == account.domain.lower() else domain.lower()
    existing = site_apps.current(account, domain)
    if domain and not _owns_domain(request, account, domain):
        raise Http404
    if existing is not None and existing.state != AppState.FAILED:
        return _site_redirect("setup_status", account, domain)
    if account.status != HostingStatus.ACTIVE:
        messages.error(request, "This website is not active yet, so it cannot be set up.")
        return redirect("hosting_customer:detail", pk=account.pk)
    kind = request.POST.get("type") or request.GET.get("type") or ""
    if request.method == "POST" and kind == "custom":
        try:
            site_apps.start_custom(request.user, account, domain=domain, request=request)
        except ServiceError as exc:
            messages.error(request, exc.message)
            return _site_redirect("setup", account, domain)
        messages.success(request, "Your custom website is ready. Upload your files with the File manager or SFTP.")
        return redirect(_urls(account, domain)["done_url"])
    if kind != "wordpress":
        return _page(request, account, domain, step="type", done=[], error=existing.error if existing else "")
    if request.method == "POST":
        form = WordPressForm(request.POST)
        if form.is_valid():
            try:
                site_apps.start_wordpress(request.user, account, request=request, domain=domain, title=form.cleaned_data["title"], admin_user=form.cleaned_data["admin_user"],
                                          admin_email=form.cleaned_data["admin_email"], admin_password=form.cleaned_data["admin_password"])
            except ServiceError as exc:
                form.add_error(None, exc.message)
            else:
                return _site_redirect("setup_status", account, domain)
    else:
        form = WordPressForm(initial={"title": domain or account.domain, "admin_user": "siteadmin", "admin_email": request.user.email, "admin_password": _suggestion()})
    return _page(request, account, domain, step="details", done=["type"], form=form)


@login_required
def setup_status(request, pk, domain=""):
    account = _own(request, pk)
    domain = "" if (domain or account.domain).lower() == account.domain.lower() else domain.lower()
    app = site_apps.poll(request.user, account, domain)
    if app is None:
        return _site_redirect("setup", account, domain)
    extra = {}
    if app.state == AppState.READY:
        from urllib.parse import urlencode

        from django.urls import reverse

        extra = {"domain_state": domain_check.check(account, domain), "transfer_url": reverse("domains_public:search") + "?" + urlencode({"domain": domain or account.domain})}
    if app.kind == "wordpress":
        extra["progress"] = site_apps.install_progress(app)
    return _page(request, account, domain, step="install", done=["type", "details"] if app.kind == "wordpress" else ["type", "details", "install"], app=app, **extra)
