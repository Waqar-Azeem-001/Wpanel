"""Reactions to billing events: a paid invoice applies its service changes; a cancelled one voids them."""
from django.dispatch import receiver

from apps.billing.signals import invoice_cancelled, invoice_paid


@receiver(invoice_paid)
def apply_paid_changes(sender, invoice, actor=None, **kwargs):
    from . import services

    services.apply_paid_invoice(invoice, actor=actor)


@receiver(invoice_cancelled)
def void_cancelled_changes(sender, invoice, actor=None, **kwargs):
    from . import services

    services.void_changes(invoice)
