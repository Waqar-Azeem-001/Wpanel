"""Customer pages: their invoices and quotes, paying online or reporting an offline payment."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core import portal
from apps.core.exceptions import ServiceError
from apps.core.web import ACTION_ERRORS, error_text, run_action

from . import forms, invoicing, payments, pdf
from .gateways import PaymentError, get_adapter
from .models import OPEN_STATUSES, InvoiceStatus, PaymentMethod, QuoteStatus, Transaction, TransactionStatus


def _pdf_response(content, filename):
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}.pdf"'
    return response


def _invoice_or_404(request, pk):
    return get_object_or_404(invoicing.visible_invoices_for_user(request.user), pk=pk)


def _billing_panel(request):
    return portal.actions_panel(request, "Billing", [
        ("My invoices", "billing_customer:invoice_list", "bi-receipt"),
        ("My quotes", "billing_customer:quote_list", "bi-file-earmark-text"),
        ("Payment methods", "billing_customer:payment_methods", "bi-credit-card")])


def _quote_or_404(request, pk):
    return get_object_or_404(invoicing.visible_quotes_for_user(request.user), pk=pk)


# --- Invoices ---------------------------------------------------------------------------------

@login_required
def invoice_list(request):
    queryset = invoicing.visible_invoices_for_user(request.user).order_by("-created_at", "-id")
    choices = [("unpaid", "Unpaid"), ("overdue", "Overdue"), (InvoiceStatus.PAID, "Paid"),
               (InvoiceStatus.CANCELLED, "Cancelled"), (InvoiceStatus.REFUNDED, "Refunded")]

    def apply(rows, value):  # "unpaid" is everything still owing, including a part payment; "overdue" is derived
        if value == "unpaid":
            return rows.filter(status__in=OPEN_STATUSES)
        return invoicing.filter_invoices_by_status(rows, value)

    queryset, view_panel = portal.status_filter(request, queryset, choices, url_name="billing_customer:invoice_list",
                                                all_label="All invoices", apply=apply)
    return render(request, "billing/customer/invoice_list.html", {
        "page": Paginator(queryset, 25).get_page(request.GET.get("page")),
        "sidebar": [view_panel, _billing_panel(request)]})


@login_required
def invoice_detail(request, pk):
    invoice = _invoice_or_404(request, pk)
    offline, online = payments.active_payment_methods_for(invoice) if invoice.can_be_paid else ([], [])
    return render(request, "billing/customer/invoice_detail.html", {
        "invoice": invoice, "items": invoice.items.all(),
        "transactions": invoice.transactions.order_by("-occurred_at", "-id"),
        "offline_methods": offline, "online_methods": online,
        "pending_report": invoice.transactions.filter(status=TransactionStatus.PENDING, provider__isnull=True).exists(),
        "form": forms.ReportPaymentForm(initial={"amount": invoice.balance_due})})


@login_required
def invoice_pdf(request, pk):
    invoice = _invoice_or_404(request, pk)
    return _pdf_response(pdf.render_invoice_pdf(invoice), invoice.number or f"invoice-{invoice.pk}")


@require_POST
@login_required
def invoice_pay(request, pk):
    """Pay online (redirects to the gateway) or report an offline payment, depending on the method chosen."""
    invoice = _invoice_or_404(request, pk)
    form = forms.ReportPaymentForm(request.POST)
    try:
        if not form.is_valid():
            raise ServiceError("Choose a payment method and check the amount.")
        data = form.cleaned_data
        method = PaymentMethod.objects.filter(code=data["method"], is_active=True).select_related("provider").first()
        if method is None:
            raise ServiceError("Choose a valid payment method.", code="payment_method_invalid")
        if method.provider_id:
            _, url = payments.start_gateway_payment(request.user, invoice, method, request=request)
            return redirect(url)
        payments.report_payment(request.user, invoice, method=method, amount=data["amount"],
                                reference=data["reference"], note=data["note"], request=request)
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
    else:
        messages.success(request, "Thank you. We will confirm your payment as soon as we receive it.")
    return redirect("billing_customer:invoice_detail", pk=pk)


# --- Test gateway (simulated hosted checkout) ---------------------------------------------------

@login_required
def test_gateway(request, external_id):
    """A stand-in for a gateway's hosted page: "pay" or "fail" sends our own signed webhook. Dev/test only."""
    if not settings.ALLOW_TEST_PAYMENT_GATEWAY:
        raise Http404
    tx = get_object_or_404(Transaction.objects.select_related("invoice", "client", "provider"),
                           external_id=external_id, provider__kind="test")
    if not invoicing.can_act_for_client(request.user, tx.client):
        raise Http404
    if request.method == "POST":
        outcome = request.POST.get("outcome")
        if outcome not in ("succeeded", "failed"):
            raise Http404
        try:
            adapter = get_adapter(tx.provider)
            body, headers = adapter.simulate_event(f"payment.{outcome}", tx.external_id, tx.amount, tx.currency,
                                                   reason="Declined on the test gateway page.")
            result = payments.handle_webhook(tx.provider, body, headers)
        except (PaymentError, ServiceError) as exc:
            messages.error(request, error_text(exc))
        else:
            if outcome == "succeeded" and result.status == "processed":
                messages.success(request, "Payment received. Thank you.")
            elif outcome == "failed":
                messages.error(request, "The payment was declined. You can try again.")
            else:
                messages.info(request, "This payment was already processed.")
        return redirect("billing_customer:invoice_detail", pk=tx.invoice_id)
    return render(request, "billing/customer/test_gateway.html", {"tx": tx})


# --- Quotes -----------------------------------------------------------------------------------

@login_required
def quote_list(request):
    queryset = invoicing.visible_quotes_for_user(request.user).order_by("-created_at", "-id")
    choices = [(QuoteStatus.SENT, "Sent"), (QuoteStatus.ACCEPTED, "Accepted"), (QuoteStatus.DECLINED, "Declined"),
               (QuoteStatus.CANCELLED, "Cancelled")]
    queryset, view_panel = portal.status_filter(request, queryset, choices, url_name="billing_customer:quote_list",
                                                all_label="All quotes")
    return render(request, "billing/customer/quote_list.html", {
        "page": Paginator(queryset, 25).get_page(request.GET.get("page")),
        "sidebar": [view_panel, _billing_panel(request)]})


@login_required
def quote_detail(request, pk):
    quote = _quote_or_404(request, pk)
    return render(request, "billing/customer/quote_detail.html", {
        "quote": quote, "items": quote.items.all(), "invoices": quote.invoices.all()})


@login_required
def quote_pdf(request, pk):
    quote = _quote_or_404(request, pk)
    return _pdf_response(pdf.render_quote_pdf(quote), quote.number or f"quote-{quote.pk}")


@require_POST
@login_required
def quote_accept(request, pk):
    quote = _quote_or_404(request, pk)

    def action():
        invoice = invoicing.accept_quote(request.user, quote, request=request)
        return f"Quote accepted. Invoice {invoice.number} has been issued."

    return run_action(request, action, "billing_customer:quote_detail", pk=pk)


@require_POST
@login_required
def quote_decline(request, pk):
    quote = _quote_or_404(request, pk)

    def action():
        invoicing.decline_quote(request.user, quote, request=request)
        return "Quote declined."

    return run_action(request, action, "billing_customer:quote_detail", pk=pk)


@login_required
def payment_methods(request):
    """How to pay us: the methods a customer can use, with the instructions for each."""
    offline, online = payments.active_payment_methods()
    return render(request, "billing/customer/payment_methods.html", {"offline_methods": offline, "online_methods": online})
