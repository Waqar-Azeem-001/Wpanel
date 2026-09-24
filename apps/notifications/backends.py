from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend


class NoProviderConfiguredBackend(BaseEmailBackend):
    """Production fallback: refuse to send when no email provider is active."""

    def send_messages(self, email_messages):
        raise ImproperlyConfigured("No active email provider is configured. Add one in the admin portal.")
