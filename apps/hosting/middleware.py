from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from .admin_access import AdminAccessRequired


class AdminAccessMiddleware:
    """A staff member who opens a customer's hosting screen without Admin Access is sent to the staff service page to start it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, AdminAccessRequired):
            return None
        messages.info(request, "To manage this customer's hosting, start Admin Access below. It needs a reason, lasts an hour and is recorded.")
        return redirect(reverse("hosting_staff:detail", args=[exception.account.pk]))
