"""Account and support: tickets, the AI Agent, people, callbacks, passwords and notifications."""
C = "account-support"

ARTICLES = [
    (C, "How to open a support ticket", "Open a support ticket, choose the right department and follow our reply by email and in your account.", True, """A ticket is the best way to ask our team for help with something that needs a person.

## Open a ticket

1. Open **Support > Open a ticket**.
2. Choose the **department**: Technical, Billing, Sales or General.
3. Choose the related service or domain, if any.
4. Write a clear subject and describe the problem.
5. Attach screenshots if they help, and send.

## Follow it

We reply by email and in your account. Open **Support > Tickets** to read replies, add details or close the ticket.

## Frequently asked questions

### How fast will you reply?
We reply as soon as we can; urgent issues should say so in the subject.

### Can I reply by email?
Replies in the panel keep everything in one place.

### Can I reopen a ticket?
Reply to it again within the allowed time, or open a new one.
"""),

    (C, "How to write a good support ticket", "Get faster help by telling us the domain, the exact error, when it happened and what you already tried.", False, """A clear ticket gets a faster and better answer.

## Include these things

1. **The domain or service** the problem is about.
2. **What you expected** and **what happened instead**.
3. **The exact error message**, or a screenshot.
4. **When** it started and whether anything changed before it, such as an update.
5. **What you already tried.**

## Examples

Bad: "My website is broken."
Good: "example.com shows a 500 error since 10:00 today, after I updated the Elementor plugin. I switched the plugin off but it still shows."

## One problem per ticket

Open separate tickets for separate problems so each can be solved and closed.

## Frequently asked questions

### Should I include my password?
Never. If we need access we will tell you the safe way.

### Can I add files?
Yes, screenshots and small logs help.

### Where is my reply?
In your email and in Support > Tickets.
"""),

    (C, "How to use the AI Agent", "Ask the AI Agent in English, Urdu or Roman Urdu: it checks your account, tells you the steps in the panel and can prepare changes you approve.", True, """The **AI Agent** button at the top of every page opens a chat that helps you with your account.

## What it can do

- Answer how-to questions about the panel and tell you the steps.
- Check your website, SSL, disk use, error log, DNS records, mailboxes and invoices.
- Open a ticket, or ask a person from our team to join the chat.

## Changes

If you ask for a change, the Agent first tells you how to do it yourself in the panel. If you say "kar do" or that you did not understand, it prepares the change and shows a card:

- For simple changes you press **Approve**.
- For sensitive ones (DNS records, restoring a backup, deleting a mailbox) we email you a **6 digit code**. Press **Verify securely** and enter it on the page, never in the chat.

## Your privacy

The Agent looks only at your own account. Never type a password, card number or security code in the chat.

## Frequently asked questions

### Which languages can I use?
English, Urdu and Roman Urdu.

### Is there a limit?
A daily message limit keeps the service fair. The chat tells you how many are left.

### Can I talk to a real person?
Yes. See [[How to talk to a person or ask for a callback]].
"""),

    (C, "How to talk to a person or ask for a callback", "Reach a real person from the chat, or ask our team to phone you at a time that suits you.", False, """Sometimes you would rather speak to someone.

## Talk to a person

In the AI Agent chat press **Talk to a person**. If someone from our team is online they join the same chat and you see their name. If nobody is online we open a ticket with your chat attached so you do not repeat yourself.

## Ask for a callback

1. Press **Request a callback**.
2. Write your phone number.
3. Choose a day and time in the next 14 days.
4. Add a short note and press the button.

Our team phones you around that time.

## Frequently asked questions

### Can I continue on WhatsApp?
If we have turned it on, the chat shows a **Continue on WhatsApp** link.

### How many callbacks can I ask for?
Up to three waiting at a time.

### What if I miss the call?
Ask for another time or open a ticket.
"""),

    (C, "How to change your hosting password", "Set a new password for cPanel, FTP and SSH of your hosting plan yourself, so it never passes through a chat.", False, """You set a hosting password yourself, so it never passes through a chat or an email.

## Steps

1. Open **Websites** and choose your hosting plan.
2. Open the plan's **Information** page and press **Change password**.
3. Type a strong password of at least 12 characters and save.

## What it covers

The new password works for cPanel, FTP and SSH of that plan. WordPress has its own password: see [[How to set a new WordPress password]].

## Good to know

Update saved passwords in your FTP program and any app that uses it. Never send a password to anyone, including our team.

## Frequently asked questions

### Will my website stop?
No. Websites do not use this password.

### Can the AI Agent change it?
No. For your safety you set it yourself.

### I forgot my account password?
Use the "Forgot your password?" link on the sign-in page.
"""),

    (C, "How to update your profile and billing details", "Keep your name, email, phone and billing address correct so invoices, notices and renewals reach you.", False, """Correct details make sure invoices and important notices reach you.

## Update them

1. Open the menu at the top right and choose your profile.
2. Change your name, email, phone and address.
3. Save.

## Billing details

The billing name and address appear on invoices. Update them before an invoice is issued.

## Frequently asked questions

### Will old invoices change?
No. Issued invoices keep the details they were made with.

### What if I change my email?
You will need to verify the new address.

### Can I add a second email for invoices?
Ask us in a ticket; an alternate address can receive copies.
"""),

    (C, "How to choose which notifications you receive", "Decide which emails and in-app notices you get, while important billing and security messages always reach you.", False, """You can control most notifications.

## Where

Open the menu at the top right and choose **Notifications** or your notification settings.

## What you can choose

For each kind of message you can choose email, in-app notice or both. Some messages cannot be switched off because they are important: security codes, billing and service-continuity messages.

## The bell

The bell icon at the top shows unread notices. Open it to read and mark them as read.

## Frequently asked questions

### Why do I still get billing emails?
They are essential for keeping your services running.

### Can I get fewer emails?
Switch off the optional kinds.

### Where did a notice go?
Open the bell and the notifications page.
"""),

    (C, "Keep your account safe: passwords, codes and phishing", "Protect your customer account: strong passwords, never sharing security codes and spotting phishing emails.", False, """Your account holds your websites, domains and payment history. Protect it.

## Do

- Use a **strong, unique password**. See [[How to create strong passwords and keep them safe]].
- Sign out on shared computers.
- Check the address bar before you type a password: it should be our own address.
- Keep your email address safe: it can reset your password.

## Do not

- Never share a password, card number or **security code** with anyone, in a chat, by phone or by email. Our team never asks for them.
- Do not click links in emails you did not expect.

## Spot phishing

Fake emails copy our look and ask you to "verify" or pay urgently. Open the panel by typing our address yourself and check there.

## Frequently asked questions

### I got a code I did not ask for?
Ignore it. Nothing happens unless you enter it.

### I think someone knows my password?
Change it now and open a ticket.

### Is the AI Agent safe?
It sees only your account and never asks for secrets.
"""),

    (C, "Our policies: terms, acceptable use, refunds and privacy", "Where to read the terms of service, acceptable use, refund, shipping and return policies that apply to your account.", False, """Our policies are linked at the bottom of every page.

## The main ones

- **Terms of Service**: the agreement between you and us.
- **Acceptable Use Policy**: what you may and may not do on our servers.
- **Refund Policy**: when and how refunds are made. See [[Refunds and cancellation policy explained]].
- **Privacy**: how we handle your data.

## Why they matter

They explain, for example, why spam or illegal content can lead to suspension, and how long data is kept after cancellation.

## Frequently asked questions

### Where are they?
In the footer of every page.

### Can they change?
We tell you about important changes.

### Who can I ask?
Open a ticket in the General department.
"""),
]
