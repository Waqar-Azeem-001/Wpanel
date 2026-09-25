"""
Events other apps react to, so billing never imports them.

``invoice_paid``: an invoice has just become fully paid (sent from inside the
transaction that recorded the payment). ``invoice_cancelled``: an unpaid invoice
was cancelled. Receivers must be idempotent.
"""
from django.dispatch import Signal

invoice_paid = Signal()
invoice_cancelled = Signal()
