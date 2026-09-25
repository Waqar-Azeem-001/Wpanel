"""
The catalogue of business events that can reach a person (roadmap Phase 11: "centralize communication").

Business code never decides on its own how, whether or to whom to send something. It names an *event* from this
registry and calls ``services.dispatch`` (or ``notify_team`` / ``dispatch_client``); the registry says which email
template the event uses, whether it is *essential* (account security, money and service-continuity messages a person
cannot switch off), and whether its email carries *sensitive* content that must not linger in the database.

Adding a new kind of message is therefore one entry here plus one call where it happens - and a test
(``test_every_event_used_in_code_is_registered``) fails if code sends an event that is not listed.
"""
from dataclasses import dataclass


class Category:
    ACCOUNT = "account"
    BILLING = "billing"
    ORDERS = "orders"
    SERVICES = "services"
    SUPPORT = "support"
    TEAM = "team"


CATEGORY_LABELS = {
    Category.ACCOUNT: "Account & security",
    Category.BILLING: "Billing & payments",
    Category.ORDERS: "Orders",
    Category.SERVICES: "Services & domains",
    Category.SUPPORT: "Support tickets",
    Category.TEAM: "Team alerts (staff)",
}
# What a person may switch off. Account, billing and service-continuity messages are essential and always sent.
USER_CONTROLLED = (Category.ORDERS, Category.SERVICES, Category.SUPPORT, Category.TEAM)


@dataclass(frozen=True)
class Event:
    key: str
    label: str
    category: str
    email_template: str = ""
    essential: bool = False
    sensitive: bool = False  # the email carries a secret (password, one-time link): stored encrypted, wiped once sent
    default_email: bool = True
    default_in_app: bool = True


def _events(*items):
    return {event.key: event for event in items}


C = Category
EVENTS = _events(
    # --- Account & security ----------------------------------------------------------------------------
    Event("client.welcome", "Welcome, with a link to set your password", C.ACCOUNT, "client_welcome", essential=True,
          sensitive=True),
    Event("account.verification", "Verify your email address", C.ACCOUNT, "verify_email", essential=True,
          sensitive=True),
    Event("account.password_reset", "Reset your password", C.ACCOUNT, "password_reset", essential=True,
          sensitive=True),
    # --- Billing & payments (always sent) -----------------------------------------------------------------
    Event("invoice.issued", "A new invoice", C.BILLING, "invoice_issued", essential=True),
    Event("invoice.due_soon", "An invoice is due soon", C.BILLING, "invoice_due_soon", essential=True),
    Event("invoice.overdue", "An invoice is overdue", C.BILLING, "invoice_overdue", essential=True),
    Event("payment.received", "Payment received", C.BILLING, "payment_received", essential=True),
    Event("payment.failed", "A payment did not go through", C.BILLING, "payment_failed", essential=True),
    Event("payment.rejected", "A reported payment was not found", C.BILLING, "payment_rejected", essential=True),
    Event("payment.refunded", "A refund was issued", C.BILLING, "payment_refunded", essential=True),
    Event("quote.sent", "A new quote", C.BILLING, "quote_sent", essential=True),
    # --- Orders -----------------------------------------------------------------------------------------------
    Event("order.placed", "Order confirmation", C.ORDERS, "order_placed", essential=True),
    Event("order.cancelled", "An order was cancelled", C.ORDERS, "order_cancelled", essential=True),
    Event("order.active", "Your order is active", C.ORDERS, "order_active"),
    # --- Services & domains -------------------------------------------------------------------------------------
    Event("hosting.welcome", "Your hosting account login details", C.SERVICES, "hosting_welcome", essential=True,
          sensitive=True),
    Event("hosting.suspended", "A hosting account was suspended", C.SERVICES, "hosting_suspended", essential=True),
    Event("hosting.terminated", "A hosting account was terminated", C.SERVICES, "hosting_terminated", essential=True),
    Event("hosting.unsuspended", "A hosting account was reactivated", C.SERVICES, "hosting_unsuspended"),
    Event("cancellation.requested", "We received your cancellation request", C.SERVICES,
          "cancellation_requested", essential=True),
    Event("cancellation.approved", "Your cancellation request was approved", C.SERVICES, "cancellation_approved",
          essential=True),
    Event("cancellation.rejected", "Your cancellation request was declined", C.SERVICES, "cancellation_rejected",
          essential=True),
    Event("cancellation.completed", "A domain cancellation was completed", C.SERVICES, "cancellation_completed",
          essential=True),
    Event("service.grace_notice", "Final notice before a service is suspended", C.SERVICES, "service_grace_notice",
          essential=True),
    Event("domain.registered", "A domain was registered or transferred in", C.SERVICES, "domain_registered"),
    Event("service.renewed", "A service was renewed", C.SERVICES, "service_renewed"),
    Event("service.upgraded", "A service was upgraded", C.SERVICES, "service_upgraded"),
    # --- Support tickets -----------------------------------------------------------------------------------------
    Event("ticket.opened", "We received your ticket", C.SUPPORT, "ticket_opened"),
    Event("ticket.reply", "A reply on your ticket", C.SUPPORT, "ticket_reply"),
    # --- Team alerts (staff; in-app by default, email only if a person asks for it) ------------------------------
    Event("ticket.assigned", "A ticket was assigned to you", C.TEAM, default_email=False),
    Event("ticket.unassigned", "A new ticket needs an owner", C.TEAM, default_email=False),
    Event("ticket.customer_reply", "A customer replied to your ticket", C.TEAM, default_email=False),
    Event("ticket.note", "A note was added to your ticket", C.TEAM, default_email=False),
    Event("payment.reported", "A customer says they have paid", C.TEAM, default_email=False),
    Event("quote.accepted", "A quote was accepted", C.TEAM, default_email=False),
    Event("quote.declined", "A quote was declined", C.TEAM, default_email=False),
    Event("order.failed", "An order could not be fully fulfilled", C.TEAM, default_email=False),
    Event("cancellation.requested_team", "A customer asked to cancel a service", C.TEAM, default_email=False),
    Event("email.failed", "Emails are failing to send", C.TEAM, default_email=False),
)


class UnknownEvent(KeyError):
    """Code tried to send an event that is not in the registry."""


def get(key):
    try:
        return EVENTS[key]
    except KeyError:
        raise UnknownEvent(f"Unknown notification event: {key!r}. Add it to apps/notifications/events.py.") from None


def sensitive_templates():
    return {event.email_template for event in EVENTS.values() if event.sensitive and event.email_template}


def events_in(category):
    return [event for event in EVENTS.values() if event.category == category]
