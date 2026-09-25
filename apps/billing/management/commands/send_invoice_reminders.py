from django.core.management.base import BaseCommand

from apps.billing import reminders


class Command(BaseCommand):
    help = "Send today's payment reminders (due soon / overdue) for open invoices."

    def handle(self, *args, **options):
        result = reminders.send_invoice_reminders()
        if result.get("disabled"):
            self.stdout.write("Payment reminders are switched off in the billing settings.")
        else:
            self.stdout.write(f"Reminders sent: {result['sent']}; already sent: {result['skipped']}.")
