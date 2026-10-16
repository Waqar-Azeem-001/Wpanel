"""Email: mailboxes, webmail, mail programs, forwarding, spam, routing and deliverability."""
C = "email"

ARTICLES = [
    (C, "How to create a mailbox and open webmail", "Create a professional email address on your own domain, set its size and password, and read your mail in the browser with webmail.", True, """A mailbox is a professional email address such as `you@yourdomain.com`. You can create one in a minute.

## Create a mailbox

1. Open **Emails** and choose your domain.
2. Press **Create mailbox**.
3. Write the name before the @, choose a strong password and a size (quota).
4. Save. The mailbox works right away.

## Open webmail

Open the mailbox page and press **Webmail** to read and send email in your browser. You do not need to install anything.

## Change the size or the password

Open the mailbox in the list. You can change its **size** or set a **new password** there.

## How many mailboxes can I have?

If your plan limits the number of mailboxes, the page shows how many you can still create.

## Frequently asked questions

### Can I use my mailbox on my phone?
Yes. See [[How to set up email on your phone or Outlook]].

### What is a good size for a mailbox?
1 to 5 GB is typical for one person. Raise it later if needed.

### Is my password stored?
It is stored securely by the mail server. We never show it.
"""),

    (C, "How to set up email on your phone or Outlook", "Add your mailbox to your phone, Outlook, Apple Mail or Thunderbird with IMAP or POP3 and the mail settings shown on the mailbox page.", True, """Every mailbox page shows its **mail settings**: the incoming and outgoing server names, the ports and the security type. Use them to add the mailbox to any mail program.

## Steps

1. Open **Emails**, choose the mailbox and read its mail settings.
2. In your phone or in Outlook, add a new account and choose **IMAP** (or POP3 if you prefer, see [[IMAP or POP3: which should you choose?]]).
3. Enter the full email address as the user name and the mailbox password.
4. Enter the incoming and outgoing server names and ports shown on the page.
5. Use the secure (SSL/TLS) ports.

## Common problems

- **Cannot send**: the outgoing server must ask for the same user name and password.
- **Password rejected**: type the full address as the user name.
- **Certificate warning**: use the server name shown on the mailbox page.

## Frequently asked questions

### Do I need to install anything on my phone?
No. The built-in mail app works.

### Can I use two phones?
Yes, with IMAP both stay in sync.

### Where is the mail settings page?
On the mailbox page in Emails.
"""),

    (C, "IMAP or POP3: which should you choose?", "Learn the difference between IMAP and POP3 for reading email, and which one suits one device, many devices and limited storage.", False, """Both IMAP and POP3 let a mail program read your email, but they work differently.

## IMAP

IMAP keeps your mail on the server and shows it in every device. Reading, deleting and folders stay in sync. This is the best choice if you use a phone and a computer.

## POP3

POP3 downloads the mail to one computer, and usually removes it from the server. Good for a single device with limited server space, but you must back up that computer.

## Which should I choose?

- Several devices: **IMAP**.
- One computer and a small mailbox: POP3 can work, with "leave a copy on the server" switched on.

## Frequently asked questions

### Which uses less server space?
POP3 can, because it removes mail after downloading.

### Can I switch later?
Yes. Add the account again as IMAP; messages on the server appear.

### Does webmail matter?
Webmail always shows what is on the server.
"""),

    (C, "How to forward email to another address", "Create an email forwarder so mail sent to one address is copied to another, for example to your Gmail.", False, """A **forwarder** copies mail sent to one address to another address.

## Steps

1. Open **Emails** and choose your domain.
2. Open **Forwarders** and press **Add forwarder**.
3. Write the address that receives the mail (for example `info@yourdomain.com`).
4. Write the address to send copies to.
5. Save.

## Good to know

- You do not need a mailbox for the first address: a forwarder alone is enough.
- A forwarder to many people is a simple mailing list.
- To stop forwarding, delete the forwarder.

## Frequently asked questions

### Will the sender see the forward?
No. They see the original address.

### Can I forward to several addresses?
Create one forwarder for each target.

### Why do forwarded messages go to spam?
The receiving provider may distrust forwarded mail. Set up SPF and DKIM. See [[How to stop your email going to spam]].
"""),

    (C, "How to set up an automatic reply (autoresponder)", "Send an automatic message to people who write to you, for example while you are on holiday, and switch it off when you are back.", False, """An **autoresponder** replies automatically to people who write to you.

## Steps

1. Open **Emails** and choose your domain.
2. Open **Autoresponders** and press **Add**.
3. Choose the address, write the subject and the message.
4. Save.

## Good practice

- Say when you will be back and who to contact meanwhile.
- Keep it short.
- Switch it off when you return by deleting the autoresponder.

## Frequently asked questions

### Does it reply to every message?
It replies once to each sender in a period to avoid loops.

### Can I schedule it?
Set it up before you leave and delete it when you return.

### Does it reply to spam?
It may. A good spam filter helps. See [[How to adjust your spam filter]].
"""),

    (C, "What to do when a mailbox is full", "Free space in a full mailbox, clean spam and trash, download attachments or make the mailbox bigger.", False, """A mailbox that is full stops receiving new mail and senders may see a bounce message.

## Free space

- Delete old messages and empty the **Trash** and **Spam** folders. They use space too.
- Download large attachments and delete the mail.
- Move old mail into an archive on your computer.

## Make it bigger

Open **Emails**, choose the mailbox and change its **size (quota)**. If your plan has a total limit, the page tells you how much is left.

## Let the AI Agent help

Ask the AI Agent to increase a mailbox size: it explains the steps and can do it for you after you approve. See [[How to use the AI Agent]].

## Frequently asked questions

### Why does the size look full when I have few messages?
Attachments and the Trash and Spam folders add up.

### Can I have unlimited size?
It depends on your plan. The page shows the limits.

### Will I lose mail while it is full?
Senders get an error and can resend. Free space quickly.
"""),

    (C, "How to adjust your spam filter", "Change how strict the spam filter is for your domain, and keep good mail out of the spam folder.", False, """The spam filter checks incoming mail and moves suspicious messages to the Spam folder.

## Open the settings

Open **Emails**, choose your domain and open **Spam settings**.

## What you can change

The page shows the spam settings your server offers for the domain, such as whether the filter is on and how it treats suspicious mail. A stricter setting catches more spam but may also catch good mail, so change one setting at a time and watch the result for a few days.

## Good habits

- Check the Spam folder now and then for good messages.
- Mark good mail as "not spam" in webmail.
- Never reply to spam.

## Frequently asked questions

### Why is good mail going to spam?
The sender may lack SPF or DKIM. Ask them to fix it, or lower the strictness.

### Can I block one sender?
Create a filter or a block rule in webmail.

### Is spam stored forever?
No. Old spam is removed after a while.
"""),

    (C, "How mail routing works: local or remote delivery", "Choose whether your domain's email is kept on our server (local) or delivered to another provider (remote), and avoid lost email.", False, """**Mail routing** tells our server what to do with mail for your domain.

## The choices

- **Local**: the mail is delivered to mailboxes here. Use this when your mailboxes are on this hosting.
- **Remote**: the mail is sent on to another provider named in your MX records, for example Google Workspace or Microsoft 365.

## Where to set it

Open **Emails**, choose your domain and open **Mail routing**.

## When to change it

Switch to **remote** after you point your MX records to another provider. If you leave it on local, mail for addresses without a mailbox here may be rejected.

## Frequently asked questions

### What if I choose the wrong one?
Mail may bounce or arrive in the wrong place. Switch back and wait a few minutes.

### Does it affect sending?
No, only receiving.

### Do I need MX records too?
Yes. See [[How to add MX records for email]].
"""),

    (C, "How to stop your email going to spam", "Improve email delivery with SPF, DKIM and DMARC records, a clean sender reputation and good habits so your messages reach the inbox.", True, """If your emails land in your customers' spam folders, a few fixes usually solve it.

## 1. Add SPF

SPF lists the servers allowed to send for your domain. Add one TXT record on `@`. See [[How to add TXT records for SPF, DKIM, DMARC and verification]].

## 2. Add DKIM

DKIM signs each message so receivers know it is genuine and unchanged.

## 3. Add DMARC

DMARC says what to do with mail that fails the checks and sends you reports.

## 4. Send good mail

- Use a real "From" address on your domain.
- Avoid ALL CAPS and many exclamation marks.
- Do not use spammy words or large images only.
- Include an unsubscribe link in bulk mail.

## 5. Watch your reputation

Do not send to old or purchased lists. Remove addresses that bounce.

## Frequently asked questions

### How do I test my mail?
Send a message to a free mail account and check the headers for SPF, DKIM and DMARC "pass".

### Why does Gmail still mark me as spam?
Reputation takes time. Keep sending wanted mail and ask recipients to mark you as "not spam".

### Do I need a dedicated IP?
Not for normal use.
"""),

    (C, "Why you cannot send or receive email: a checklist", "Fix email that does not arrive or will not send: check the mailbox, password, quota, MX records, routing and spam folder step by step.", False, """When email stops working, go through this list in order.

## Cannot receive

1. **Is the mailbox full?** See [[What to do when a mailbox is full]].
2. **MX records.** Open DNS and check there is an MX record that points where you expect.
3. **Mail routing.** The domain must be set to local if the mailboxes are here. See [[How mail routing works: local or remote delivery]].
4. **Spam folder.** Check it in webmail.
5. **Typing.** Ask the sender to check the address.

## Cannot send

1. **Password and user name.** Use the full address as the user name.
2. **Ports and security.** Use the secure ports shown on the mailbox page.
3. **Limits.** Your plan may limit emails per hour.
4. **Blocked by recipient.** Read the bounce message: it names the reason.

## Still stuck?

Open a ticket with the address, the time and the exact error text. See [[How to write a good support ticket]].

## Frequently asked questions

### What does "550" in a bounce mean?
The receiving server refused the message. The text after it says why.

### Webmail works but my phone does not?
Check the phone's password and server settings.

### Is the problem on my side?
Try webmail: if it works, the server is fine and the issue is in the mail program.
"""),

    (C, "How to delete a mailbox safely", "Delete a mailbox you no longer need, and save its messages first so nothing important is lost.", False, """Deleting a mailbox removes the address and every message in it. It cannot be undone.

## Before you delete

- Download or forward the mail you need.
- Tell people the address will stop working.
- Create a forwarder to a new address if people still write to it.

## Steps

1. Open **Emails** and choose your domain.
2. Find the mailbox and press **Delete**.
3. Confirm.

## Let the AI Agent do it

You can ask the AI Agent to delete a mailbox. It tells you the steps first, then needs a security code from your email, because the action is permanent.

## Frequently asked questions

### Can I get the mail back?
Only from a backup made earlier. See [[How to create and download a backup]].

### What happens to mail sent after deletion?
It bounces unless there is a forwarder or a catch-all.

### Do I free up space?
Yes. The mailbox's space becomes free.
"""),

    (C, "Email limits and good habits for sending", "Know your plan's email limits, avoid being blocked and send newsletters and bulk mail the right way.", False, """To protect everyone, hosting plans limit how much email can be sent each hour.

## Where to see your limits

Open **Emails** and choose your domain. The page shows what your plan allows, such as the number of mailboxes and forwarders and the largest mailbox size.

## Good habits

- Send newsletters through a dedicated email service, not through a normal mailbox.
- Remove addresses that bounce.
- Keep your website and passwords safe: hacked sites often send spam and get blocked.
- Use strong mailbox passwords.

## If you are blocked

If your account sends too much email, sending can be limited for a while. Fix the cause (for example a hacked website) and tell us.

## Frequently asked questions

### Can I send 1,000 emails at once?
Use a newsletter service for that.

### How do I know I was hacked?
Unexpected bounce messages and a long mail queue are signs. Run a malware scan. See [[How to scan your website for malware and use quarantine]].

### Do forwarders count?
Forwarded messages count as sent messages.
"""),
]
