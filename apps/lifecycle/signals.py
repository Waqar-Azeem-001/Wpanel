"""A paid renewal lifts a suspension that was made for non-payment."""
from django.dispatch import receiver

from apps.renewals.signals import service_renewed


@receiver(service_renewed)
def lift_nonpayment_suspension(sender, change, service, **kwargs):
    from . import services

    services.lift_suspension_after_payment(service)
