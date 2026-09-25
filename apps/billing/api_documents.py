"""
REST API for invoices, transactions, quotes, billable items and billing settings,
plus the payment-gateway webhook endpoint. As everywhere, views only translate
requests into service calls; ``invoicing`` and ``payments`` enforce every rule
(who may see or do what included).
"""
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django_filters import rest_framework as filters
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.roles import perm
from apps.clients.models import Client
from apps.core.exceptions import ServiceError, error_body
from apps.core.permissions import HasPortalPermission

from . import invoicing, payments, pdf
from .gateways import WebhookVerificationError
from .models import (BillableItem, BillingSettings, DiscountType, Invoice, InvoiceItem, InvoiceStatus,
                     PaymentMethod, PaymentProvider, Quote, QuoteItem, QuoteStatus, Transaction, TransactionStatus,
                     TransactionType)


# --- Output ---------------------------------------------------------------------------------------

class LineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceItem
        fields = ["id", "description", "quantity", "unit_price", "amount", "discount_amount", "taxable",
                  "tax_amount", "total"]
        read_only_fields = fields


class QuoteLineSerializer(LineSerializer):
    class Meta(LineSerializer.Meta):
        model = QuoteItem


class TransactionSerializer(serializers.ModelSerializer):
    code = serializers.CharField(read_only=True)
    type = serializers.ChoiceField(choices=TransactionType.choices, read_only=True)
    status = serializers.ChoiceField(choices=TransactionStatus.choices, read_only=True)
    invoice_id = serializers.IntegerField(read_only=True)
    client_id = serializers.IntegerField(read_only=True)
    parent_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Transaction
        fields = ["id", "code", "invoice_id", "client_id", "type", "status", "amount", "currency", "method_name",
                  "reference", "note", "failure_reason", "parent_id", "occurred_at", "created_at"]
        read_only_fields = fields


class _DocumentSerializer(serializers.ModelSerializer):
    client_id = serializers.IntegerField(read_only=True)
    number = serializers.CharField(read_only=True)
    reference = serializers.CharField(read_only=True)
    billing = serializers.SerializerMethodField()

    def get_billing(self, doc) -> dict:
        return {f.removeprefix("billing_"): getattr(doc, f) for f in
                ("billing_name", "billing_company", "billing_email", "billing_phone", "billing_address_line1",
                 "billing_address_line2", "billing_city", "billing_state", "billing_postcode", "billing_country",
                 "billing_tax_id")}


class InvoiceSerializer(_DocumentSerializer):
    status = serializers.ChoiceField(choices=InvoiceStatus.choices, read_only=True)
    display_status = serializers.CharField(read_only=True)
    order_id = serializers.IntegerField(read_only=True)
    quote_id = serializers.IntegerField(read_only=True)
    balance_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    overpaid = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    items = LineSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = ["id", "reference", "number", "client_id", "order_id", "quote_id", "status", "display_status",
                  "currency", "issue_date", "due_date", "subtotal", "discount_total", "discount_label", "tax_name",
                  "tax_rate", "tax_total", "total", "amount_paid", "amount_refunded", "balance_due", "overpaid",
                  "paid_at", "notes", "cancel_reason", "billing", "items", "created_at", "updated_at"]
        read_only_fields = fields


class QuoteSerializer(_DocumentSerializer):
    status = serializers.ChoiceField(choices=QuoteStatus.choices, read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    items = QuoteLineSerializer(many=True, read_only=True)

    class Meta:
        model = Quote
        fields = ["id", "reference", "number", "client_id", "status", "is_expired", "currency", "issue_date",
                  "valid_until", "subtotal", "discount_total", "discount_label", "tax_name", "tax_rate", "tax_total",
                  "total", "notes", "billing", "items", "decided_at", "created_at", "updated_at"]
        read_only_fields = fields


class BillableItemSerializer(serializers.ModelSerializer):
    client_id = serializers.IntegerField(read_only=True)
    invoice_id = serializers.IntegerField(read_only=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = BillableItem
        fields = ["id", "client_id", "description", "quantity", "unit_price", "amount", "taxable", "invoice_id",
                  "created_at"]
        read_only_fields = fields


# --- Input ----------------------------------------------------------------------------------------

class LineInputSerializer(serializers.Serializer):
    description = serializers.CharField(max_length=255)
    quantity = serializers.IntegerField(min_value=1, max_value=100000, default=1)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    taxable = serializers.BooleanField(default=True)


class DocumentInputSerializer(serializers.Serializer):
    items = LineInputSerializer(many=True, allow_empty=False, max_length=invoicing.MAX_LINES)
    discount_type = serializers.ChoiceField(choices=DiscountType.choices, required=False, allow_blank=True,
                                            default="")
    discount_value = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True,
                                              default=None)
    discount_label = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="", max_length=2000)

    def service_kwargs(self):
        data = self.validated_data
        return {"lines": [dict(line) for line in data["items"]], "discount_type": data["discount_type"],
                "discount_value": data["discount_value"], "discount_label": data["discount_label"],
                "notes": data["notes"]}


class InvoiceInputSerializer(DocumentInputSerializer):
    client = serializers.IntegerField(min_value=1, required=False, help_text="Required when creating.")


class QuoteInputSerializer(DocumentInputSerializer):
    client = serializers.IntegerField(min_value=1, required=False, help_text="Required when creating.")
    valid_until = serializers.DateField(required=False, allow_null=True, default=None)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="", max_length=500)


class RecordPaymentSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    method = serializers.SlugField(required=False, allow_blank=True, default="",
                                   help_text="Payment method code. Blank = manual.")
    reference = serializers.CharField(required=False, allow_blank=True, default="", max_length=200,
                                      help_text="The transaction ID or bank reference.")
    received_at = serializers.DateTimeField(required=False, allow_null=True, default=None,
                                            help_text="When the money arrived. Blank = now; never in the future.")
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=500)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, default="", max_length=100,
                                            help_text="Or send an Idempotency-Key header.")


class PaySerializer(serializers.Serializer):
    method = serializers.SlugField(help_text="Payment method code.")
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True,
                                      help_text="Offline methods only. Blank = the full balance.")
    reference = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=500)


class PayResultSerializer(serializers.Serializer):
    transaction = TransactionSerializer()
    redirect_url = serializers.CharField(allow_null=True, help_text="Where to send the customer to pay online.")


class RefundSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True,
                                      help_text="Blank = everything still refundable on the payment.")
    reason = serializers.CharField(required=False, allow_blank=True, default="", max_length=500)


class BillableItemInputSerializer(serializers.Serializer):
    client = serializers.IntegerField(min_value=1)
    description = serializers.CharField(max_length=255)
    quantity = serializers.IntegerField(min_value=1, max_value=100000, default=1)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    taxable = serializers.BooleanField(default=True)


class InvoiceBillableSerializer(serializers.Serializer):
    client = serializers.IntegerField(min_value=1)
    items = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False,
                                  help_text="Billable item ids. Blank = all of the client's uninvoiced items.")


class BillingSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingSettings
        fields = ["company_name", "address", "email", "phone", "tax_id", "invoice_prefix", "quote_prefix",
                  "payment_terms_days", "quote_validity_days", "invoice_footer", "updated_at"]
        read_only_fields = ["updated_at"]


# --- Helpers --------------------------------------------------------------------------------------

def _client_or_404(pk):
    return get_object_or_404(Client, pk=pk)


def _required_client(serializer):
    client_id = serializer.validated_data.get("client")
    if not client_id:
        raise ServiceError("Say which client this is for.", code="client_required")
    client = Client.objects.filter(pk=client_id).first()
    if client is None:
        raise ServiceError("That client does not exist.", code="not_found", status_code=404)
    return client


def _method(code):
    if not code:
        return None
    method = PaymentMethod.objects.filter(code=code).first()
    if method is None:
        raise ServiceError("That payment method does not exist.", code="payment_method_invalid")
    return method


def _pdf(content, name):
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{name}.pdf"'
    return response


# --- Invoices -------------------------------------------------------------------------------------

class InvoiceFilter(filters.FilterSet):
    status = filters.CharFilter(method="filter_status", help_text="A status, or 'overdue'.")
    client = filters.NumberFilter(field_name="client_id")
    order = filters.NumberFilter(field_name="order_id")
    q = filters.CharFilter(method="filter_q")

    class Meta:
        model = Invoice
        fields = []

    def filter_status(self, queryset, name, value):
        return invoicing.filter_invoices_by_status(queryset, value)

    def filter_q(self, queryset, name, value):
        return invoicing.search_invoices(queryset, value)


class InvoiceViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                     mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """
    Staff (``view_billing``) see every invoice; customers see their own clients' *issued* invoices.
    Creating, editing (drafts only), issuing, cancelling and recording payments need ``manage_billing``.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = InvoiceSerializer
    filterset_class = InvoiceFilter
    ordering_fields = ["created_at", "issue_date", "due_date", "total"]
    http_method_names = ["get", "post", "put", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Invoice.objects.none()
        return invoicing.visible_invoices_for_user(self.request.user).prefetch_related("items")

    @extend_schema(request=InvoiceInputSerializer, responses={201: InvoiceSerializer})
    def create(self, request, *args, **kwargs):
        serializer = InvoiceInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invoice = invoicing.create_invoice(request.user, _required_client(serializer),
                                           request=request, **serializer.service_kwargs())
        return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=InvoiceInputSerializer, responses=InvoiceSerializer)
    def update(self, request, *args, **kwargs):
        serializer = InvoiceInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invoice = invoicing.update_invoice(request.user, self.get_object(), request=request,
                                           **serializer.service_kwargs())
        return Response(InvoiceSerializer(invoice).data)

    def destroy(self, request, *args, **kwargs):
        invoicing.delete_draft_invoice(request.user, self.get_object(), request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=None, responses=InvoiceSerializer)
    @action(detail=True, methods=["post"])
    def issue(self, request, *args, **kwargs):
        invoice = invoicing.issue_invoice(request.user, self.get_object(), request=request)
        return Response(InvoiceSerializer(invoice).data)

    @extend_schema(request=ReasonSerializer, responses=InvoiceSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, *args, **kwargs):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invoice = invoicing.cancel_invoice(request.user, self.get_object(), reason=serializer.validated_data["reason"],
                                           request=request)
        return Response(InvoiceSerializer(invoice).data)

    @extend_schema(responses={(200, "application/pdf"): OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"], filter_backends=[])
    def pdf(self, request, *args, **kwargs):
        invoice = self.get_object()
        return _pdf(pdf.render_invoice_pdf(invoice), invoice.number or f"invoice-{invoice.pk}")

    @extend_schema(responses=TransactionSerializer(many=True))
    @action(detail=True, methods=["get"], filter_backends=[], pagination_class=None)
    def transactions(self, request, *args, **kwargs):
        rows = self.get_object().transactions.order_by("-occurred_at", "-id")
        return Response(TransactionSerializer(rows, many=True).data)

    @extend_schema(request=RecordPaymentSerializer, responses={201: TransactionSerializer})
    @action(detail=True, methods=["post"], url_path="record-payment")
    def record_payment(self, request, *args, **kwargs):
        serializer = RecordPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        key = data["idempotency_key"] or request.headers.get("Idempotency-Key", "")
        tx = payments.record_payment(request.user, self.get_object(), amount=data["amount"],
                                     method=_method(data["method"]), reference=data["reference"], note=data["note"],
                                     occurred_at=data["received_at"], idempotency_key=key, request=request)
        return Response(TransactionSerializer(tx).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=PaySerializer, responses={200: PayResultSerializer, 201: PayResultSerializer})
    @action(detail=True, methods=["post"])
    def pay(self, request, *args, **kwargs):
        """Pay online (returns a redirect URL) or report an offline payment (creates a pending transaction)."""
        serializer = PaySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        invoice = self.get_object()
        method = _method(data["method"])
        if method is not None and method.provider_id:
            tx, url = payments.start_gateway_payment(request.user, invoice, method, request=request)
            return Response({"transaction": TransactionSerializer(tx).data, "redirect_url": url})
        tx = payments.report_payment(request.user, invoice, method=method, amount=data.get("amount"),
                                     reference=data["reference"], note=data["note"], request=request)
        return Response({"transaction": TransactionSerializer(tx).data, "redirect_url": None},
                        status=status.HTTP_201_CREATED)


# --- Transactions ---------------------------------------------------------------------------------

class TransactionFilter(filters.FilterSet):
    status = filters.ChoiceFilter(choices=TransactionStatus.choices)
    type = filters.ChoiceFilter(choices=TransactionType.choices)
    invoice = filters.NumberFilter(field_name="invoice_id")
    client = filters.NumberFilter(field_name="client_id")

    class Meta:
        model = Transaction
        fields = []


class TransactionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Staff see every transaction; customers see their own clients'. Confirm/reject/refund need ``manage_billing``."""

    permission_classes = [IsAuthenticated]
    serializer_class = TransactionSerializer
    filterset_class = TransactionFilter
    ordering_fields = ["occurred_at", "amount"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Transaction.objects.none()
        return payments.visible_transactions_for_user(self.request.user)

    @extend_schema(request=None, responses=TransactionSerializer)
    @action(detail=True, methods=["post"])
    def confirm(self, request, *args, **kwargs):
        return Response(TransactionSerializer(payments.confirm_payment(
            request.user, self.get_object(), request=request)).data)

    @extend_schema(request=ReasonSerializer, responses=TransactionSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, *args, **kwargs):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(TransactionSerializer(payments.reject_payment(
            request.user, self.get_object(), reason=serializer.validated_data["reason"], request=request)).data)

    @extend_schema(request=RefundSerializer, responses={201: TransactionSerializer})
    @action(detail=True, methods=["post"])
    def refund(self, request, *args, **kwargs):
        serializer = RefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        refund = payments.refund_payment(request.user, self.get_object(), amount=data.get("amount"),
                                         reason=data["reason"], request=request)
        return Response(TransactionSerializer(refund).data, status=status.HTTP_201_CREATED)


# --- Quotes ---------------------------------------------------------------------------------------

class QuoteFilter(filters.FilterSet):
    status = filters.ChoiceFilter(choices=QuoteStatus.choices)
    client = filters.NumberFilter(field_name="client_id")
    q = filters.CharFilter(method="filter_q")

    class Meta:
        model = Quote
        fields = []

    def filter_q(self, queryset, name, value):
        return invoicing.search_quotes(queryset, value)


class QuoteViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                   mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = QuoteSerializer
    filterset_class = QuoteFilter
    ordering_fields = ["created_at", "valid_until", "total"]
    http_method_names = ["get", "post", "put", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Quote.objects.none()
        return invoicing.visible_quotes_for_user(self.request.user).prefetch_related("items")

    @extend_schema(request=QuoteInputSerializer, responses={201: QuoteSerializer})
    def create(self, request, *args, **kwargs):
        serializer = QuoteInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        quote = invoicing.create_quote(request.user, _required_client(serializer), request=request,
                                       valid_until=serializer.validated_data["valid_until"],
                                       **serializer.service_kwargs())
        return Response(QuoteSerializer(quote).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=QuoteInputSerializer, responses=QuoteSerializer)
    def update(self, request, *args, **kwargs):
        serializer = QuoteInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        quote = invoicing.update_quote(request.user, self.get_object(), request=request,
                                       valid_until=serializer.validated_data["valid_until"],
                                       **serializer.service_kwargs())
        return Response(QuoteSerializer(quote).data)

    @extend_schema(request=None, responses=QuoteSerializer)
    @action(detail=True, methods=["post"])
    def send(self, request, *args, **kwargs):
        return Response(QuoteSerializer(invoicing.send_quote(request.user, self.get_object(),
                                                             request=request)).data)

    @extend_schema(request=None, responses=InvoiceSerializer)
    @action(detail=True, methods=["post"])
    def accept(self, request, *args, **kwargs):
        """Accept the quote; returns the invoice it produced."""
        invoice = invoicing.accept_quote(request.user, self.get_object(), request=request)
        return Response(InvoiceSerializer(invoice).data)

    @extend_schema(request=None, responses=QuoteSerializer)
    @action(detail=True, methods=["post"])
    def decline(self, request, *args, **kwargs):
        return Response(QuoteSerializer(invoicing.decline_quote(request.user, self.get_object(),
                                                                request=request)).data)

    @extend_schema(request=None, responses=QuoteSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, *args, **kwargs):
        return Response(QuoteSerializer(invoicing.cancel_quote(request.user, self.get_object(),
                                                               request=request)).data)

    @extend_schema(responses={(200, "application/pdf"): OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"], filter_backends=[])
    def pdf(self, request, *args, **kwargs):
        quote = self.get_object()
        return _pdf(pdf.render_quote_pdf(quote), quote.number or f"quote-{quote.pk}")


# --- Billable items -------------------------------------------------------------------------------

class BillableItemViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                          mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Staff only: one-off charges waiting to be invoiced."""

    permission_classes = [HasPortalPermission]
    serializer_class = BillableItemSerializer
    queryset = BillableItem.objects.all()
    ordering_fields = ["created_at"]

    @property
    def required_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [perm("view_billing")]
        return [perm("manage_billing")]

    @extend_schema(request=BillableItemInputSerializer, responses={201: BillableItemSerializer})
    def create(self, request, *args, **kwargs):
        serializer = BillableItemInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        item = invoicing.create_billable_item(request.user, _client_or_404(data["client"]),
                                              description=data["description"], quantity=data["quantity"],
                                              unit_price=data["unit_price"], taxable=data["taxable"],
                                              request=request)
        return Response(BillableItemSerializer(item).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        invoicing.delete_billable_item(request.user, self.get_object(), request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=InvoiceBillableSerializer, responses={201: InvoiceSerializer})
    @action(detail=False, methods=["post"])
    def invoice(self, request):
        """Collect a client's uninvoiced items onto a new draft invoice."""
        serializer = InvoiceBillableSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        invoice = invoicing.invoice_billable_items(request.user, _client_or_404(data["client"]),
                                                   data.get("items"), request=request)
        return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)


# --- Settings -------------------------------------------------------------------------------------

class BillingSettingsView(APIView):
    """The seller details and numbering defaults printed on documents."""

    permission_classes = [HasPortalPermission]

    @property
    def required_permissions(self):
        return [perm("view_billing")] if self.request.method in ("GET", "HEAD", "OPTIONS") else [
            perm("manage_billing")]

    @extend_schema(responses=BillingSettingsSerializer)
    def get(self, request):
        return Response(BillingSettingsSerializer(BillingSettings.load()).data)

    @extend_schema(request=BillingSettingsSerializer, responses=BillingSettingsSerializer)
    def put(self, request):
        serializer = BillingSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = invoicing.save_billing_settings(request.user, dict(serializer.validated_data), request=request)
        return Response(BillingSettingsSerializer(row).data)


# --- Gateway webhook ------------------------------------------------------------------------------

class WebhookResultSerializer(serializers.Serializer):
    status = serializers.CharField()
    detail = serializers.CharField(allow_blank=True)


class PaymentWebhookView(APIView):
    """
    Where a payment gateway delivers its events. There is no login: the request is
    authenticated by the gateway's signature over the raw body, verified with the
    provider's webhook secret. A bad signature is a 400 and changes nothing; any
    verified event - including a redelivery - is acknowledged with 200.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    @extend_schema(request=OpenApiTypes.BINARY, responses={200: WebhookResultSerializer}, auth=[],
                   description="Signed gateway event. The body is read raw; the signature header is gateway specific.")
    def post(self, request, provider_id):
        provider = PaymentProvider.objects.filter(pk=provider_id, is_active=True).first()
        if provider is None:
            raise ServiceError("Unknown payment provider.", code="not_found", status_code=404)
        try:
            result = payments.handle_webhook(provider, request.body, request.headers)
        except WebhookVerificationError:
            return Response(error_body("invalid_signature", "The webhook signature could not be verified."),
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({"status": result.status, "detail": result.detail})
