"""Server-rendered domain pages (public search, customer self-service, staff). Rules live in ``services``."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.clients.services import single_contact_client
from apps.core import bulk, portal
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError

from . import forms, services
from .models import Domain, DnsRecord, DomainSettings, DomainStatus, TldPricing


def _apply_error(form, exc):
    if isinstance(exc, ValidationError) and hasattr(exc, "error_dict"):
        for field, errors in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    elif isinstance(exc, ValidationError):
        form.add_error(None, exc)
    else:
        form.add_error(None, exc.message)


def _run(request, action, view_name, **redirect_kwargs):
    """Run a POST action, flash the outcome, and redirect to ``view_name``. ``action`` takes no args."""
    try:
        message = action()
    except (ServiceError, ValidationError) as exc:
        messages.error(request, exc.message if isinstance(exc, ServiceError) else "; ".join(exc.messages))
    else:
        if message:
            messages.success(request, message)
    return redirect(view_name, **redirect_kwargs)




# --- Public: availability search -----------------------------------------------------------

def _typed_name(raw):
    """What a visitor typed as a domain name, made tidy: no address prefix, no path, lower case, and a name with no extension (``mybrand``) gets the first extension we sell (.com when we sell it)."""
    name = (raw or "").strip().lower()
    for prefix in ("https://", "http://"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    name = name.split("/")[0].split("?")[0]
    if name.startswith("www."):
        name = name[4:]
    if name and "." not in name:
        active = [t.tld for t in TldPricing.objects.filter(is_active=True).order_by("tld")]
        if active:
            name += ".com" if ".com" in active else active[0]
    return name


def _suggestions(result, popular, limit=5):
    """The same name under the other popular extensions, each checked: [{domain, available, pricing}]. A check that fails is left out, never an error."""
    base = result["domain"].split(".")[0]
    found = []
    for tld in popular:
        name = base + tld.tld
        if name == result["domain"]:
            continue
        try:
            available, pricing = services.check_availability(name)
        except ServiceError:
            continue
        found.append({"domain": name, "available": available, "pricing": pricing})
        if len(found) == limit:
            break
    return found


def domain_search(request):
    form = forms.AvailabilitySearchForm(request.GET or None)
    result = None
    if request.GET and form.is_valid():
        typed = _typed_name(form.cleaned_data["domain"])
        try:
            available, pricing = services.check_availability(typed)
        except ServiceError as exc:
            form.add_error("domain", exc.message)
        except ValidationError as exc:
            form.add_error("domain", " ".join(exc.messages))
        else:
            result = {"available": available, "pricing": pricing, "domain": typed, "years": form.cleaned_data["years"]}
    tlds = list(TldPricing.objects.filter(is_active=True).order_by("tld"))
    by_name = {t.tld: t for t in tlds}
    from apps.products.models import Addon, AddonKind, CatalogStatus
    from apps.products.categories import lowest_price
    from apps.products.views import PREFERRED_TLDS

    popular = [by_name[x] for x in PREFERRED_TLDS if x in by_name][:6] or sorted(tlds, key=lambda t: t.register_price)[:6]
    whois = [lowest_price(a) for a in Addon.objects.filter(kind=AddonKind.WHOIS_PRIVACY, status=CatalogStatus.ACTIVE).prefetch_related("prices")]
    whois = [p for p in whois if p is not None]
    suggestions = _suggestions(result, popular) if result else []
    return render(request, "domains/public/search.html", {
        "form": form, "result": result, "suggestions": suggestions, "tlds": tlds, "popular": popular, "cheapest": min(tlds, key=lambda t: t.register_price) if tlds else None,
        "whois_price": min(whois, key=lambda p: p.price) if whois else None})


# --- Customer self-service -----------------------------------------------------------------

@login_required
def my_domain_list(request):
    domains = Domain.objects.filter(client__contacts__user=request.user).select_related("client").distinct()
    domains, view_panel = portal.status_filter(request, domains, DomainStatus.choices, url_name="domains_customer:list",
                                               all_label="All domains")
    return render(request, "domains/customer/list.html", {"domains": domains, "sidebar": [view_panel]})


def _own_domain(request, pk):
    return get_object_or_404(services.visible_domains_for_user(request.user), pk=pk)


def _tab(request, pk, tab, **extra):
    """One page of a domain's tabbed view. Self-service edits make sense right up to a terminal state; PENDING domains can
    still have nameservers/DNS adjusted before staff completes registration/transfer."""
    domain = _own_domain(request, pk)
    context = {"domain": domain, "tab": tab, "sidebar": portal.domain_sidebar(request, domain),
               "can_edit": domain.status not in (DomainStatus.CANCELLED, DomainStatus.FAILED), **extra}
    return render(request, f"domains/customer/tab_{tab}.html", context)


@login_required
def my_domain_detail(request, pk):
    from apps.products.upsell import whois_privacy_offer

    return _tab(request, pk, "overview", whois_offer=whois_privacy_offer(domain=_own_domain(request, pk)))


@login_required
def my_domain_auto_renew(request, pk):
    if request.method != "POST":
        return _tab(request, pk, "auto_renew")
    domain = _own_domain(request, pk)
    enabled = request.POST.get("auto_renew") == "on"

    def action():
        services.set_auto_renew(request.user, domain, enabled, request=request)
        return "Auto-renew updated."

    return _run(request, action, "domains_customer:auto_renew", pk=pk)


@login_required
def my_domain_nameservers(request, pk):
    if request.method != "POST":
        domain = _own_domain(request, pk)
        initial = {"nameservers": "\n".join(domain.nameservers)}
        return _tab(request, pk, "nameservers", nameservers_form=forms.NameserversForm(initial=initial))
    domain = _own_domain(request, pk)
    form = forms.NameserversForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Enter 2-13 valid nameservers.")
        services.update_nameservers(request.user, domain, form.cleaned_data["nameservers"], request=request)
        return "Nameservers updated."

    return _run(request, action, "domains_customer:nameservers", pk=pk)


@login_required
def my_domain_lock_tab(request, pk):
    return _tab(request, pk, "lock")


@require_POST
@login_required
def my_domain_lock(request, pk, locked):
    domain = _own_domain(request, pk)
    fn = services.lock_domain if locked else services.unlock_domain

    def action():
        fn(request.user, domain, request=request)
        return f"Domain {'locked' if locked else 'unlocked'}."

    return _run(request, action, "domains_customer:lock_tab", pk=pk)


@login_required
def my_domain_renew_tab(request, pk):
    return _tab(request, pk, "renew", years=range(1, 6))


@login_required
def my_domain_dns(request, pk):
    if request.method != "POST":
        return _tab(request, pk, "dns", dns_form=forms.DnsRecordForm())
    domain = _own_domain(request, pk)
    form = forms.DnsRecordForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Enter a valid DNS record.")
        services.add_dns_record(request.user, domain, form.cleaned_data, request=request)
        return "DNS record added."

    return _run(request, action, "domains_customer:dns_add", pk=pk)


@require_POST
@login_required
def my_domain_dns_remove(request, pk, record_id):
    domain = _own_domain(request, pk)
    record = get_object_or_404(DnsRecord, pk=record_id, domain=domain)

    def action():
        services.delete_dns_record(request.user, domain, record, request=request)
        return "DNS record removed."

    return _run(request, action, "domains_customer:dns_add", pk=pk)


# --- Staff -------------------------------------------------------------------------------

@portal_permission_required(perm("view_domains"))
def staff_domain_list(request):
    filter_form = forms.DomainFilterForm(request.GET or None)
    queryset = Domain.objects.select_related("client")
    if filter_form.is_valid():
        queryset = services.search_domains(queryset, filter_form.cleaned_data["q"])
        if filter_form.cleaned_data["status"]:
            queryset = queryset.filter(status=filter_form.cleaned_data["status"])
    page = Paginator(queryset.order_by("-created_at"), 25).get_page(request.GET.get("page"))
    return render(request, "domains/staff/list.html", {"filter_form": filter_form, "page": page,
                                                        "can_manage": request.user.has_perm(perm("manage_domains"))})


@require_POST
@portal_permission_required(perm("manage_domains"))
def staff_domain_bulk(request):
    """"With selected": cancel every ticked domain that is still pending or failed (the same rule as one at a
    time - a live or already-registered domain is reported as a failure, not silently skipped). There is no bulk
    delete here: almost every real domain is protected by the order/invoice it came from, so "cancel" - which
    already works regardless of that history - is the meaningful bulk action for a domain a person wants gone."""
    if request.POST.get("do") != "cancel":
        messages.error(request, "Choose what to do with the selected domains.")
    else:
        bulk.run(request, Domain.objects.all(), bulk.selected_ids(request),
                 lambda d: services.cancel_domain_request(request.user, d, request=request),
                 verb="domain request(s) cancelled", label=lambda d: d.name)
    return bulk.back_to(request, "domains_staff:list")


@portal_permission_required(perm("view_domains"))
def staff_domain_detail(request, pk):
    domain = get_object_or_404(Domain.objects.select_related("client"), pk=pk)
    can_manage = request.user.has_perm(perm("manage_domains"))
    return render(request, "domains/staff/detail.html", {
        "domain": domain,
        "can_manage": can_manage,
        "nameservers_form": forms.NameserversForm(initial={"nameservers": "\n".join(domain.nameservers)}),
        "dns_form": forms.DnsRecordForm(),
        "renew_form": forms.RenewDomainForm(),
        "cancel_form": forms.CancelDomainForm(),
    })


@require_POST
@portal_permission_required(perm("manage_domains"))
def staff_domain_complete(request, pk):
    domain = get_object_or_404(Domain, pk=pk)

    def action():
        if domain.status == "pending_transfer_in":
            services.complete_transfer(request.user, domain, request=request)
        else:
            services.complete_registration(request.user, domain, request=request)
        return "Completed."

    return _run(request, action, "domains_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_domains"))
def staff_domain_cancel(request, pk):
    domain = get_object_or_404(Domain, pk=pk)
    form = forms.CancelDomainForm(request.POST)

    def action():
        reason = form.cleaned_data.get("reason", "") if form.is_valid() else ""
        services.cancel_domain_request(request.user, domain, reason=reason, request=request)
        return "Request cancelled."

    return _run(request, action, "domains_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_domains"))
def staff_domain_renew(request, pk):
    domain = get_object_or_404(Domain, pk=pk)
    form = forms.RenewDomainForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Enter a valid number of years.")
        services.renew_domain(request.user, domain, form.cleaned_data["years"], request=request)
        return "Domain renewed."

    return _run(request, action, "domains_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_domains"))
def staff_domain_sync(request, pk):
    domain = get_object_or_404(Domain, pk=pk)

    def action():
        services.sync_domain(request.user, domain, request=request)
        return "Synced."

    return _run(request, action, "domains_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("view_domains"))  # can_self_service() inside the service allows manage_domains
def staff_domain_nameservers(request, pk):
    domain = get_object_or_404(Domain, pk=pk)
    form = forms.NameserversForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Enter 2-13 valid nameservers.")
        services.update_nameservers(request.user, domain, form.cleaned_data["nameservers"], request=request)
        return "Nameservers updated."

    return _run(request, action, "domains_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("view_domains"))
def staff_domain_lock(request, pk, locked):
    domain = get_object_or_404(Domain, pk=pk)
    fn = services.lock_domain if locked else services.unlock_domain

    def action():
        fn(request.user, domain, request=request)
        return f"Domain {'locked' if locked else 'unlocked'}."

    return _run(request, action, "domains_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("view_domains"))
def staff_domain_dns_add(request, pk):
    domain = get_object_or_404(Domain, pk=pk)
    form = forms.DnsRecordForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Enter a valid DNS record.")
        services.add_dns_record(request.user, domain, form.cleaned_data, request=request)
        return "DNS record added."

    return _run(request, action, "domains_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("view_domains"))
def staff_domain_dns_remove(request, pk, record_id):
    domain = get_object_or_404(Domain, pk=pk)
    record = get_object_or_404(DnsRecord, pk=record_id, domain=domain)

    def action():
        services.delete_dns_record(request.user, domain, record, request=request)
        return "DNS record removed."

    return _run(request, action, "domains_staff:detail", pk=pk)


# --- Staff: TLD pricing ----------------------------------------------------------------------

@portal_permission_required(perm("view_domains"))
def staff_tld_list(request):
    tlds = TldPricing.objects.all()
    can_manage = request.user.has_perm(perm("manage_domains"))
    from . import price_sync

    return render(request, "domains/staff/tld_list.html", {
        "tlds": tlds, "can_manage": can_manage, "last_sync": price_sync.last_run(), "sync_source": price_sync.source_url(),
        "form": forms.TldPricingForm() if can_manage else None,
    })


@require_POST
@portal_permission_required(perm("manage_domains"))
def staff_tld_sync(request):
    """Read the company website's price list now (the same job that runs every night)."""
    from . import price_sync

    result = price_sync.run_and_record(actor=request.user, force=request.POST.get("force") == "1")
    if result["ok"]:
        text = f"Prices read: {len(result['created'])} added, {len(result['updated'])} updated, {result['unchanged']} already right."
        if result["held"]:
            text += f" {len(result['held'])} held back because the price moved more than 3 times: " + ", ".join(h["tld"] for h in result["held"][:8]) + "."
        messages.success(request, text)
    else:
        messages.error(request, result["error"])
    return redirect("domains_staff:tld_list")


@require_POST
@portal_permission_required(perm("manage_domains"))
def staff_tld_save(request):
    form = forms.TldPricingForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Enter valid pricing.")
        data = dict(form.cleaned_data)
        tld = data.pop("tld")
        services.set_tld_pricing(request.user, tld, request=request, **data)
        return f"Pricing saved for {tld}."

    return _run(request, action, "domains_staff:tld_list")


@require_POST
@portal_permission_required(perm("manage_domains"))
def staff_tld_status(request, pk):
    pricing = get_object_or_404(TldPricing, pk=pk)
    enabled = request.POST.get("is_active") == "on"

    def action():
        services.set_tld_pricing_active(request.user, pricing, enabled, request=request)
        return "Updated."

    return _run(request, action, "domains_staff:tld_list")


@portal_permission_required(perm("view_settings"))
def staff_settings(request):
    row = DomainSettings.load()
    can_manage = request.user.has_perm(perm("manage_settings"))
    # Not "request.POST or None": the only field is a checkbox, so unchecking it and submitting posts no keys at
    # all - an empty-but-real POST, which must still bind the form (and clear the setting), not be treated as a GET.
    form = forms.DomainSettingsForm(request.POST if request.method == "POST" else None,
                                    initial={"auto_register": row.auto_register})
    if request.method == "POST":
        if not can_manage:
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        if form.is_valid():
            try:
                services.save_settings(request.user, request=request, **form.cleaned_data)
            except ServiceError as exc:
                _apply_error(form, exc)
            else:
                messages.success(request, "Saved.")
                return redirect("domains_staff:settings")
    return render(request, "domains/staff/settings.html", {"form": form, "can_manage": can_manage})


@portal_permission_required(perm("view_domains"))
def staff_whois(request):
    """A raw WHOIS lookup (Phase 17): not a registrar operation, just the public WHOIS system - see ``whois.py``."""
    from . import whois

    name = request.GET.get("q", "").strip()
    result, error = "", ""
    if name:
        try:
            result = whois.lookup(name)
        except whois.WhoisError as exc:
            error = str(exc)
    return render(request, "domains/staff/whois.html", {"q": name, "result": result, "error": error})
