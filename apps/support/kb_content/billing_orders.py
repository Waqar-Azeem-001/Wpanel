"""Billing and orders: invoices, payments, renewals, upgrades, refunds and the affiliate programme."""
C = "billing-orders"

ARTICLES = [
    (C, "How to pay an invoice online", "Pay your hosting or domain invoice online step by step, see when it is marked paid and where to find your receipt.", True, """Your invoices are in **Billing**. Paying one takes a minute.

## Pay

1. Open **Billing > Invoices**.
2. Open the unpaid invoice.
3. Press **Pay** and choose a payment method.
4. Follow the steps for that method.
5. The invoice shows **Paid** when we receive the payment.

## Receipts

Every paid invoice can be opened and downloaded as a PDF from the same page.

## If payment fails

Check the card details and your bank's limits, or choose another method. If money left your account but the invoice is not paid after a while, see [[How to report a bank transfer payment]].

## Frequently asked questions

### Is my card saved?
Card details are handled by the payment provider, not stored in the panel.

### Can I pay part of an invoice?
Where a payment method allows it, a partly paid invoice shows what remains.

### Which currency is used?
The currency shown on the invoice.
"""),

    (C, "How to report a bank transfer payment", "Paid by bank transfer? Report your payment with the date and reference so we can match it and mark your invoice paid.", False, """If you pay by bank transfer, tell us once you have paid so we can match it quickly.

## Steps

1. Pay to the bank account shown on the invoice. Write the invoice number as the reference.
2. Open the invoice in **Billing**.
3. Press **Report a payment** and enter the date, the amount and the transfer reference.
4. Send. We check and mark the invoice paid.

## How long does it take?

Usually within one business day after we see the money.

## Frequently asked questions

### What if I forgot the reference?
Write the date, amount and your name; we will find it.

### Can I attach a receipt?
Add the details in the form, or open a ticket with the bank slip.

### Will my service stop meanwhile?
Report the payment before the due date and we will take it into account.
"""),

    (C, "Payment methods you can use", "See the payment methods available for your invoices and orders and how each one works.", False, """The payment methods shown at checkout and on an invoice are the ones we accept for your account.

## Where to see them

Open an invoice and press **Pay**: the available methods are listed. The footer of the website also shows the main ones.

## Types of methods

- **Online payment**: card or wallet; the invoice is paid at once.
- **Bank transfer**: pay to our account and report it. See [[How to report a bank transfer payment]].

## Frequently asked questions

### Are there fees?
If a method has a fee, it is shown before you pay.

### Can I change the method later?
Yes. Choose another method on the next invoice.

### Is it safe?
Payments go through secure payment providers.
"""),

    (C, "How to renew your hosting plan", "Renew your hosting before it ends: where renewal invoices appear, how to pay and what happens if you wait.", True, """A hosting plan must be renewed to stay active.

## How renewal works

1. A renewal invoice is created before the plan ends.
2. You get an email and see a notice on Home.
3. Pay the invoice. See [[How to pay an invoice online]].
4. The plan continues without a break.

## Where to see the date

On the plan's **Information** page and in **Billing**.

## If you do not pay in time

The plan can be suspended after the due date and later removed. A suspended plan comes back after you pay.

## Frequently asked questions

### Can I renew early?
Open the plan and use the renew option, or pay the renewal invoice when it is created.

### Will my prices change?
The renewal price is shown on the invoice.

### Can I cancel instead?
Yes. See [[Refunds and cancellation policy explained]].
"""),

    (C, "How to understand your invoice", "Read an invoice line by line: items, discounts, tax, total, due date and status, and what overdue means.", False, """An invoice shows what you are charged for and what you still owe.

## The parts of an invoice

- **Number and dates**: the invoice number, the issue date and the due date.
- **Items**: each product or service, its period and its price.
- **Discount**: a coupon or offer taken off the total.
- **Tax**: shown as its own line where it applies.
- **Total**: what you pay.
- **Paid and balance**: payments received and what remains.

## Status

- **Unpaid**: waiting for payment.
- **Partly paid**: some money received.
- **Paid**: complete.
- **Overdue**: unpaid after the due date.

## Frequently asked questions

### Why is there tax?
Tax follows the rules of your country and is shown when it applies.

### Can I change the billing details on an invoice?
Update your details in your profile before the invoice is issued.

### Can I get an invoice in a PDF?
Yes, open it and download.
"""),

    (C, "Order status explained: pending, active, failed and cancelled", "Know what each order status means, what to do when an order is stuck and how to ask us for help.", False, """Open **Orders** to see every order and its status.

## The statuses

- **Pending**: waiting for payment or for us to start.
- **Active**: paid and set up.
- **Failed**: something went wrong; you are not charged for what failed.
- **Cancelled**: the order was cancelled.

## If an order is stuck

If it is paid but still pending after a while, tell the AI Agent or open a ticket with the order number. See [[How to write a good support ticket]].

## Frequently asked questions

### How long does setup take?
Hosting is usually active within minutes after payment. Domains too, depending on the registry.

### Can I cancel an order?
Pending orders can be cancelled. For active services see the cancellation policy.

### Where is my invoice?
Each order links to its invoice.
"""),

    (C, "How to upgrade or change your hosting plan", "Move to a bigger hosting plan when your website grows, and how to get your website moved with our help.", False, """When you often reach your plan's limits it is time to upgrade.

## Signs you need more

Slow pages, resource limit warnings, no space left or no room for another website. See [[How to check your resource usage (CPU, memory and processes)]].

## Steps

1. Open **Plans** and choose the bigger plan.
2. Order and pay.
3. Tell us in a ticket that you want your website moved to the new plan and we will help.

## What happens to the old plan?

You can let it end after the move. Keep it until you confirm everything works.

## Frequently asked questions

### Will my website be offline?
Moving is planned to avoid downtime. We tell you the steps.

### Can I downgrade?
Ask in a ticket; the options depend on your plan.

### Is the move free?
Ask us before you order: the answer depends on your plan.
"""),

    (C, "What happens if an invoice is not paid", "Understand due dates, reminders, suspension and termination for unpaid invoices, and how to bring a suspended service back.", False, """Services stay active while their invoices are paid on time.

## The usual order of events

1. A reminder is sent before the due date.
2. After the due date the invoice is **overdue**.
3. If it stays unpaid the service can be **suspended**: websites and email stop.
4. After a longer time the service can be **terminated** and its data removed.

## Bring a suspended service back

Pay the invoice. The service is switched on again after payment is received.

## Avoid problems

Pay before the due date, or report a bank transfer early. See [[How to report a bank transfer payment]].

## Frequently asked questions

### Is my data lost on suspension?
No. It stays until termination.

### Can I get more time?
Open a ticket before the due date and ask.

### Do domains follow the same rules?
Domains have their own expiry rules. See [[How to renew your domain name]].
"""),

    (C, "Refunds and cancellation policy explained", "How to cancel a service, when a refund is possible and how to ask, based on our refund policy.", False, """Refunds and cancellations follow our **Refund Policy**, linked at the bottom of every page.

## Cancel a service

1. Open the service.
2. Choose **Cancel** and say when you want it to end.
3. We confirm by email.

## Ask for a refund

Open a ticket in the **Billing** department with the invoice number and the reason. A person reviews it and replies on the ticket.

## Good to know

Domain registrations and some add-ons are usually not refundable once processed. The policy lists the details.

## Frequently asked questions

### How long does a refund take?
After approval, usually within a few business days, to the original method.

### Can the AI Agent refund me?
No. Refunds are decided by a person.

### What if I cancel by mistake?
Tell us quickly in a ticket.
"""),

    (C, "How the Refer and earn programme works", "Share your personal link, earn commission on orders from people you refer, and see your earnings in the panel.", False, """Refer and earn lets you earn when people you introduce buy from us.

## How it works

1. Open **Refer and earn** and copy your personal link.
2. Share it on your website, social media or with friends.
3. When someone orders through your link you earn a commission.
4. Your earnings and referrals are shown on the same page.

## Payouts

Commission is approved after the order is paid and can be paid out according to the programme rules shown on the page.

## Frequently asked questions

### Can I refer myself?
No. Self-referrals are not allowed.

### Is there a limit?
No limit on referrals.

### Do I need to apply?
If the page asks you to apply, send the form and wait for approval.
"""),
]
