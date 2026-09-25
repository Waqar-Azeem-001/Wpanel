"""Money events decide commissions: a fully paid invoice earns one; a refund lowers or voids it."""
from django.dispatch import receiver

from apps.billing.signals import invoice_paid, invoice_refunded


@receiver(invoice_paid)
def earn_commission(sender, invoice, actor=None, **kwargs):
    from . import services

    services.on_invoice_paid(invoice)


@receiver(invoice_refunded)
def adjust_commission(sender, invoice, actor=None, **kwargs):
    from . import services

    services.on_invoice_refunded(invoice)
