"""Customer billing routes, mounted at account/billing/."""
from django.urls import path

from . import views_customer as views

app_name = "billing_customer"

urlpatterns = [
    path("invoices/", views.invoice_list, name="invoice_list"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("invoices/<int:pk>/pdf/", views.invoice_pdf, name="invoice_pdf"),
    path("invoices/<int:pk>/pay/", views.invoice_pay, name="invoice_pay"),
    path("quotes/", views.quote_list, name="quote_list"),
    path("quotes/<int:pk>/", views.quote_detail, name="quote_detail"),
    path("quotes/<int:pk>/pdf/", views.quote_pdf, name="quote_pdf"),
    path("quotes/<int:pk>/accept/", views.quote_accept, name="quote_accept"),
    path("quotes/<int:pk>/decline/", views.quote_decline, name="quote_decline"),
    path("payment-methods/", views.payment_methods, name="payment_methods"),
    path("test-gateway/<str:external_id>/", views.test_gateway, name="test_gateway"),
]
