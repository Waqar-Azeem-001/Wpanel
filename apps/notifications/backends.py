import smtplib

from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend
from django.core.mail.message import sanitize_address


class EnvelopeSMTPBackend(SMTPBackend):
    """
    Django's SMTP backend, with a separately chosen envelope sender (the address the receiving server checks SPF against). The
    visible From header is unchanged; only the SMTP ``MAIL FROM`` differs when ``envelope_from`` is set.
    """

    def __init__(self, *args, envelope_from=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.envelope_from = envelope_from

    def _send(self, email_message):
        if not self.envelope_from:
            return super()._send(email_message)
        encoding = email_message.encoding
        recipients = [sanitize_address(addr, encoding) for addr in email_message.recipients()]
        if not recipients:
            return False
        sender = sanitize_address(self.envelope_from, encoding)
        try:
            self.connection.sendmail(sender, recipients, email_message.message().as_bytes(linesep="\r\n"))
        except smtplib.SMTPException:
            if not self.fail_silently:
                raise
            return False
        return True


class NoProviderConfiguredBackend(BaseEmailBackend):
    """Production fallback: refuse to send when no email provider is active."""

    def send_messages(self, email_messages):
        raise ImproperlyConfigured("No active email provider is configured. Add one in the admin portal.")
