"""
Staff pages for invoices, payments, quotes, billable items and the billing
settings. Views only translate requests into calls to ``invoicing`` /
``payments`` (which enforce every rule) and render the result.
"""
import uuid
from datetime import datetime, time

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.clients.models import Client
from apps.clients.services import search_clients
from apps.core import bulk
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError
from apps.core.web import ACTION_ERRORS, apply_form_error, error_text, query_id, run_action

from . import forms, invoicing, payments, pdf
from .models import (BillableItem, BillingSettings, Invoice, InvoiceStatus, Quote, QuoteStatus, Transaction,
                     TransactionStatus, TransactionType)

INVOICE_STATUS_CHOICES = [(s.value, s.label) for s in InvoiceStatus] + [("overdue", "Overdue")]


def _can_manage(request):
    return request.user.has_perm(perm("manage_billing"))


def _pdf_response(content, filename):
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}.pdf"'
    return response


def _page(request, queryset, per_page=25):
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))


# --- Choosing a client for a new document ---------------------------------------------------------

def _client_picker(request, *, title, create_url):
    term = request.GET.get("q", "").strip()
    clients = search_clients(Client.objects.all(), term).order_by("company_name", "first_name", "id")[:15] if term else []
    return render(request, "billing/staff/client_picker.html", {
        "title": title, "create_url": create_url, "clients": clients, "q": term, "section": title.lower()})


# --- Invoices -------------------------------------------------------------------------------------

@portal_permission_required(perm("view_billing"))
def invoice_list(request):
    filter_form = forms.InvoiceFilterForm(request.GET or None, choices=INVOICE_STATUS_CHOICES)
    queryset = Invoice.objects.select_related("client")
    if filter_form.is_valid():
        queryset = invoicing.search_invoices(queryset, filter_form.cleaned_data["q"])
        queryset = invoicing.filter_invoices_by_status(queryset, filter_form.cleaned_data["status"])
    return render(request, "billing/staff/invoice_list.html", {
        "filter_form": filter_form, "page": _page(request, queryset.order_by("-created_at", "-id")),
        "can_manage": _can_manage(request), "section": "invoices"})


@portal_permission_required(perm("manage_billing"))
def invoice_new(request):
    return _client_picker(request, title="Invoices", create_url="billing_staff:invoice_create")


def _document_page(request, *, kind, client, document=None):
    """Create or edit a draft invoice/quote: the document form plus the line rows."""
    is_quote = kind == "quote"
    form_class = forms.QuoteForm if is_quote else forms.InvoiceForm
    initial = {}
    current_currency = document.currency if document is not None else client.currency
    if document is not None:
        initial = {"notes": document.notes, "discount_label": document.discount_label}
        if document.discount_total:
            initial.update(discount_type="fixed", discount_value=document.discount_total)
        if is_quote:
            initial["valid_until"] = document.valid_until
    posted = request.method == "POST"
    form = form_class(request.POST if posted else None, initial=initial, current_currency=current_currency)
    formset = forms.line_formset(request.POST if posted else None,
                                 existing=document.items.all() if document is not None else ())
    if posted and form.is_valid() and formset.is_valid():
        data = dict(form.cleaned_data)
        lines = forms.lines_from_formset(formset)
        try:
            common = {"lines": lines, "discount_type": data["discount_type"],
                      "discount_value": data["discount_value"], "discount_label": data["discount_label"],
                      "notes": data["notes"], "currency": data["currency"], "request": request}
            if is_quote:
                common["valid_until"] = data["valid_until"]
                saved = (invoicing.update_quote(request.user, document, **common) if document is not None
                         else invoicing.create_quote(request.user, client, **common))
            else:
                saved = (invoicing.update_invoice(request.user, document, **common) if document is not None
                         else invoicing.create_invoice(request.user, client, **common))
        except ACTION_ERRORS as exc:
            apply_form_error(form, exc)
        else:
            messages.success(request, f"{kind.capitalize()} saved as a draft.")
            return redirect(f"billing_staff:{kind}_detail", pk=saved.pk)
    return render(request, "billing/staff/document_form.html", {
        "form": form, "formset": formset, "client": client, "document": document, "kind": kind,
        "section": "quotes" if is_quote else "invoices"})


@portal_permission_required(perm("manage_billing"))
def invoice_create(request):
    client_id = query_id(request, "client")
    if client_id is None:  # typed or bookmarked without a client: choose one first
        return redirect("billing_staff:invoice_new")
    client = get_object_or_404(Client, pk=client_id)
    return _document_page(request, kind="invoice", client=client)


@portal_permission_required(perm("manage_billing"))
def invoice_edit(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("client"), pk=pk)
    if invoice.status != InvoiceStatus.DRAFT:
        messages.error(request, "Only a draft invoice can be edited.")
        return redirect("billing_staff:invoice_detail", pk=pk)
    return _document_page(request, kind="invoice", client=invoice.client, document=invoice)


@portal_permission_required(perm("view_billing"))
def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("client", "order", "quote"), pk=pk)
    txs = list(invoice.transactions.select_related("provider").order_by("-occurred_at", "-id"))
    for tx in txs:
        tx.refundable = (payments.refundable_amount(tx) if tx.type == TransactionType.PAYMENT
                         and tx.status == TransactionStatus.SUCCEEDED else 0)
    can_manage = _can_manage(request)
    payment_form = None
    if can_manage and invoice.can_be_paid:
        payment_form = forms.RecordPaymentForm(initial={"amount": invoice.balance_due,
                                                        "idempotency_key": uuid.uuid4().hex})
    return render(request, "billing/staff/invoice_detail.html", {
        "invoice": invoice, "items": invoice.items.all(), "transactions": txs, "can_manage": can_manage,
        "payment_form": payment_form, "cancel_form": forms.CancelForm(), "refund_form": forms.RefundForm(),
        "reject_form": forms.RejectForm(), "section": "invoices"})


@require_POST
@portal_permission_required(perm("manage_billing"))
def invoice_bulk(request):
    """"With selected": issue the ticked drafts, or cancel the ticked unpaid invoices (with one reason for all)."""
    do = request.POST.get("do", "")
    reason = request.POST.get("reason", "").strip()[:300]
    actions = {
        "delete": ("draft invoice(s) deleted", lambda i: invoicing.delete_draft_invoice(request.user, i, request=request)),
        "issue": ("invoice(s) issued", lambda i: invoicing.issue_invoice(request.user, i, request=request)),
        "cancel": ("invoice(s) cancelled", lambda i: invoicing.cancel_invoice(request.user, i, reason=reason, request=request)),
    }
    if do not in actions:
        messages.error(request, "Choose what to do with the selected invoices.")
    else:
        verb, action = actions[do]
        bulk.run(request, Invoice.objects.all(), bulk.selected_ids(request), action, verb=verb,
                 label=lambda i: i.number or f"Draft #{i.pk}")
    return bulk.back_to(request, "billing_staff:invoice_list")


@require_POST
@portal_permission_required(perm("manage_billing"))
def invoice_issue(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    return run_action(request, lambda: _issue(request, invoice), "billing_staff:invoice_detail", pk=pk)


def _issue(request, invoice):
    issued = invoicing.issue_invoice(request.user, invoice, request=request)
    return f"Invoice {issued.number} issued."


@require_POST
@portal_permission_required(perm("manage_billing"))
def invoice_cancel(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    form = forms.CancelForm(request.POST)

    def action():
        reason = form.cleaned_data.get("reason", "") if form.is_valid() else ""
        invoicing.cancel_invoice(request.user, invoice, reason=reason, request=request)
        return "Invoice cancelled."

    return run_action(request, action, "billing_staff:invoice_detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_billing"))
def invoice_delete(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    try:
        invoicing.delete_draft_invoice(request.user, invoice, request=request)
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
        return redirect("billing_staff:invoice_detail", pk=pk)
    messages.success(request, "Draft deleted.")
    return redirect("billing_staff:invoice_list")


@require_POST
@portal_permission_required(perm("manage_billing"))
def invoice_record_payment(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    form = forms.RecordPaymentForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Enter a valid payment: " + "; ".join(
                f"{form.fields[f].label or f}: {e[0]}" for f, e in form.errors.items()))
        data = form.cleaned_data
        received = data["received_on"]
        # A date, not a moment: today means now; an earlier day is recorded at midday on it.
        occurred_at = (timezone.make_aware(datetime.combine(received, time(12, 0)))
                       if received and received != timezone.localdate() else None)
        payments.record_payment(request.user, invoice, amount=data["amount"], method=data["method"],
                                reference=data["reference"], note=data["note"], occurred_at=occurred_at,
                                idempotency_key=data["idempotency_key"], request=request)
        return "Payment recorded."

    return run_action(request, action, "billing_staff:invoice_detail", pk=pk)


@portal_permission_required(perm("view_billing"))
def invoice_pdf(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("client", "order"), pk=pk)
    return _pdf_response(pdf.render_invoice_pdf(invoice), invoice.reference.replace(" ", "-").replace("#", ""))


# --- Payments / transactions ----------------------------------------------------------------------

@portal_permission_required(perm("view_billing"))
def transaction_list(request):
    queryset = Transaction.objects.select_related("invoice", "client")
    status_filter = request.GET.get("status", "")
    type_filter = request.GET.get("type", "")
    if status_filter in TransactionStatus.values:
        queryset = queryset.filter(status=status_filter)
    if type_filter in TransactionType.values:
        queryset = queryset.filter(type=type_filter)
    return render(request, "billing/staff/transaction_list.html", {
        "page": _page(request, queryset.order_by("-occurred_at", "-id")), "can_manage": _can_manage(request),
        "status_filter": status_filter, "type_filter": type_filter, "statuses": TransactionStatus.choices,
        "types": TransactionType.choices, "reject_form": forms.RejectForm(), "section": "transactions"})


def _transaction_action(request, pk, function, message, *, form_class=None, redirect_to=None, **fixed):
    tx = get_object_or_404(Transaction, pk=pk)
    form = form_class(request.POST) if form_class else None

    def action():
        kwargs = dict(fixed)
        if form is not None:
            if not form.is_valid():
                raise ServiceError("Please check what you entered.")
            kwargs.update({k: v for k, v in form.cleaned_data.items()})
        function(request.user, tx, request=request, **kwargs)
        return message

    target = request.POST.get("next") or ""
    if target == "invoice":
        return run_action(request, action, "billing_staff:invoice_detail", pk=tx.invoice_id)
    return run_action(request, action, "billing_staff:transaction_list")


@require_POST
@portal_permission_required(perm("manage_billing"))
def transaction_confirm(request, pk):
    return _transaction_action(request, pk, payments.confirm_payment, "Payment confirmed.")


@require_POST
@portal_permission_required(perm("manage_billing"))
def transaction_reject(request, pk):
    return _transaction_action(request, pk, payments.reject_payment, "Payment rejected.",
                               form_class=forms.RejectForm)


@require_POST
@portal_permission_required(perm("manage_billing"))
def transaction_refund(request, pk):
    return _transaction_action(request, pk, payments.refund_payment, "Refund recorded.",
                               form_class=forms.RefundForm)


# --- Quotes ---------------------------------------------------------------------------------------

@portal_permission_required(perm("view_billing"))
def quote_list(request):
    filter_form = forms.InvoiceFilterForm(request.GET or None, choices=QuoteStatus.choices)
    queryset = Quote.objects.select_related("client")
    if filter_form.is_valid():
        queryset = invoicing.search_quotes(queryset, filter_form.cleaned_data["q"])
        if filter_form.cleaned_data["status"]:
            queryset = queryset.filter(status=filter_form.cleaned_data["status"])
    return render(request, "billing/staff/quote_list.html", {
        "filter_form": filter_form, "page": _page(request, queryset.order_by("-created_at", "-id")),
        "can_manage": _can_manage(request), "section": "quotes"})


@portal_permission_required(perm("manage_billing"))
def quote_new(request):
    return _client_picker(request, title="Quotes", create_url="billing_staff:quote_create")


@portal_permission_required(perm("manage_billing"))
def quote_create(request):
    client_id = query_id(request, "client")
    if client_id is None:
        return redirect("billing_staff:quote_new")
    client = get_object_or_404(Client, pk=client_id)
    return _document_page(request, kind="quote", client=client)


@portal_permission_required(perm("manage_billing"))
def quote_edit(request, pk):
    quote = get_object_or_404(Quote.objects.select_related("client"), pk=pk)
    if quote.status != QuoteStatus.DRAFT:
        messages.error(request, "Only a draft quote can be edited.")
        return redirect("billing_staff:quote_detail", pk=pk)
    return _document_page(request, kind="quote", client=quote.client, document=quote)


@portal_permission_required(perm("view_billing"))
def quote_detail(request, pk):
    quote = get_object_or_404(Quote.objects.select_related("client"), pk=pk)
    return render(request, "billing/staff/quote_detail.html", {
        "quote": quote, "items": quote.items.all(), "invoices": quote.invoices.all(),
        "can_manage": _can_manage(request), "section": "quotes"})


@require_POST
@portal_permission_required(perm("manage_billing"))
def quote_send(request, pk):
    quote = get_object_or_404(Quote, pk=pk)

    def action():
        sent = invoicing.send_quote(request.user, quote, request=request)
        return f"Quote {sent.number} sent."

    return run_action(request, action, "billing_staff:quote_detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_billing"))
def quote_cancel(request, pk):
    quote = get_object_or_404(Quote, pk=pk)

    def action():
        invoicing.cancel_quote(request.user, quote, request=request)
        return "Quote cancelled."

    return run_action(request, action, "billing_staff:quote_detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_billing"))
def quote_delete(request, pk):
    quote = get_object_or_404(Quote, pk=pk)

    def action():
        invoicing.delete_draft_quote(request.user, quote, request=request)
        return "Draft quote deleted."

    return run_action(request, action, "billing_staff:quote_list")


@require_POST
@portal_permission_required(perm("manage_billing"))
def quote_bulk(request):
    """"With selected": delete the ticked drafts, or cancel the ticked draft/sent quotes."""
    do = request.POST.get("do", "")
    actions = {
        "delete": ("draft quote(s) deleted", lambda q: invoicing.delete_draft_quote(request.user, q, request=request)),
        "cancel": ("quote(s) cancelled", lambda q: invoicing.cancel_quote(request.user, q, request=request)),
    }
    if do not in actions:
        messages.error(request, "Choose what to do with the selected quotes.")
    else:
        verb, action = actions[do]
        bulk.run(request, Quote.objects.all(), bulk.selected_ids(request), action, verb=verb,
                 label=lambda q: q.number or f"Draft #{q.pk}")
    return bulk.back_to(request, "billing_staff:quote_list")


@portal_permission_required(perm("view_billing"))
def quote_pdf(request, pk):
    quote = get_object_or_404(Quote.objects.select_related("client"), pk=pk)
    return _pdf_response(pdf.render_quote_pdf(quote), quote.reference.replace(" ", "-").replace("#", ""))


# --- Billable items -------------------------------------------------------------------------------

@portal_permission_required(perm("view_billing"))
def billable_list(request):
    items = BillableItem.objects.select_related("client", "invoice")
    return render(request, "billing/staff/billable_list.html", {
        "page": _page(request, items.order_by("-created_at", "-id")), "can_manage": _can_manage(request),
        "form": forms.BillableItemForm(initial={"client": request.GET.get("client", "")}) if _can_manage(
            request) else None, "section": "billable"})


@require_POST
@portal_permission_required(perm("manage_billing"))
def billable_create(request):
    form = forms.BillableItemForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Enter a valid charge: " + "; ".join(
                f"{f}: {e[0]}" for f, e in form.errors.items()))
        data = form.cleaned_data
        client = Client.objects.filter(pk=data["client"]).first()
        if client is None:
            raise ServiceError("Choose an existing client (enter the client's id).", code="not_found")
        invoicing.create_billable_item(request.user, client, description=data["description"],
                                       quantity=data["quantity"], unit_price=data["unit_price"],
                                       taxable=data["taxable"], request=request)
        return "Charge recorded."

    return run_action(request, action, "billing_staff:billable_list")


@require_POST
@portal_permission_required(perm("manage_billing"))
def billable_delete(request, pk):
    item = get_object_or_404(BillableItem, pk=pk)

    def action():
        invoicing.delete_billable_item(request.user, item, request=request)
        return "Charge deleted."

    return run_action(request, action, "billing_staff:billable_list")


@require_POST
@portal_permission_required(perm("manage_billing"))
def billable_invoice(request, client_id):
    client = get_object_or_404(Client, pk=client_id)
    try:
        invoice = invoicing.invoice_billable_items(request.user, client, request=request)
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
        return redirect("billing_staff:billable_list")
    messages.success(request, "Draft invoice created from the uninvoiced charges.")
    return redirect("billing_staff:invoice_detail", pk=invoice.pk)


# --- Settings -------------------------------------------------------------------------------------

@portal_permission_required(perm("view_billing"))
def billing_settings(request):
    row = BillingSettings.load()
    can_manage = _can_manage(request)
    fields = list(forms.BillingSettingsForm.base_fields)
    form = forms.BillingSettingsForm(request.POST or None, initial={f: getattr(row, f) for f in fields})
    if request.method == "POST":
        if not can_manage:
            raise Http404
        if form.is_valid():
            try:
                invoicing.save_billing_settings(request.user, form.cleaned_data, request=request)
            except ACTION_ERRORS as exc:
                apply_form_error(form, exc)
            else:
                messages.success(request, "Billing settings saved.")
                return redirect("billing_staff:settings")
    return render(request, "billing/staff/settings.html", {"form": form, "can_manage": can_manage,
                                                           "section": "settings"})

