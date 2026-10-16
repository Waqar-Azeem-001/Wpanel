"""Domains and DNS: registering, transferring, nameservers, every common record, redirects and propagation."""
C = "domains-dns"

ARTICLES = [
    (C, "How to register a domain name", "Search for a domain name, choose the extension, order and pay. Learn how registration works, what it costs and what to do after it is registered.", True, """A domain name is your address on the internet. Registering one takes a few minutes.

## Search for a name

1. Open **Domains** and type the name you want in the search box.
2. The results show which extensions (.com, .net, .org and others) are available, with the price for each.
3. Choose the one you want. Short names without hyphens are easier to remember.

## Order and pay

Add the domain to your order, choose how many years and pay the invoice. The domain is registered after payment.

## After registration

- Point the domain to your hosting. See [[How to point your domain to our hosting]].
- Add email addresses on the domain. See [[How to create a mailbox and open webmail]].
- Keep the contact details correct. They are needed to renew or transfer.

## Choosing a good name

Keep it short, easy to spell and free of numbers and hyphens. Check that the matching social media names are free.

## Frequently asked questions

### How long does registration take?
Usually within minutes of payment.

### Is the registration yearly?
You choose the period when you order. Renewal is explained in [[How to renew your domain name]].

### Can I register the domain somewhere else and host with you?
Yes. Just point its nameservers to us.
"""),

    (C, "How to transfer a domain to us", "Move your domain from another registrar: unlock it, get the transfer code, start the transfer and what to expect during the process.", False, """You can move a domain to us from another company. The domain stays online during the transfer.

## Before you start

- The domain must be older than 60 days since its registration or last transfer.
- It must be **unlocked** at the current registrar.
- Make sure its contact email works, because the registry may send a confirmation.

## Steps

1. At the current registrar, unlock the domain and ask for the **transfer (EPP) code**.
2. In the panel open **Domains** and choose **Transfer**.
3. Enter the domain and the code and complete the order.
4. Approve the transfer if you receive an email asking you to.

## What to expect

A transfer can take from a few hours to several days. When it completes, the domain appears in your Domains list and the registration is extended by a year where the registry allows it.

## Frequently asked questions

### Will my website go offline?
No, if your DNS stays the same. Do not change nameservers until the transfer ends unless you know what you are doing.

### What if the transfer fails?
The page shows the reason. Common reasons: the domain is still locked, the code is wrong or it was registered recently.

### Do I pay again?
Transfers include a renewal period. The price is shown before you order.
"""),

    (C, "How to lock or unlock a domain and move it away", "Protect your domain with a transfer lock, and unlock it when you are ready to move it to another company with the transfer code.", False, """A **transfer lock** stops your domain from being transferred away by mistake or by someone else.

## Lock or unlock

Open **Domains**, choose your domain and press **Lock** or **Unlock**. Keep it locked unless you are about to transfer it.

## Move a domain to another company

1. **Unlock** the domain.
2. Ask for the **transfer (EPP) code**. For your safety our team gives it after confirming it is you. Open a ticket or ask the AI Agent to bring a person into the chat.
3. Give the code to the new company and approve the transfer there.

## Good to know

A domain registered or transferred in the last 60 days is usually locked by the registry and cannot be moved yet.

## Frequently asked questions

### Can the AI Agent show me the code?
No. The code is given by a person after identity checks.

### Does a lock stop my website?
No. It only affects transfers.

### Should I keep the lock on?
Yes. Unlock only before a planned transfer.
"""),

    (C, "How to change nameservers", "Change your domain's nameservers to use our DNS or another provider. Where to find them, how to set them and how long the change takes.", False, """Nameservers tell the internet where your domain's DNS records live. Changing them moves control of your DNS to a different provider.

## To use our DNS

1. Open **Domains** and choose your domain. The nameservers for your hosting are shown.
2. At your registrar replace the domain's nameservers with those.
3. Save and wait up to a few hours.

If the domain is registered with us, open the domain and use **Nameservers** to change them yourself.

## Before you change them

DNS records are read from the new place after the change. Make sure the records you need (A for your website, MX for email, TXT for verification) exist there.

## After you change them

Check your website and your email. If something does not work, compare the old records with the new ones. See [[DNS records explained: A, AAAA, CNAME, MX and TXT]].

## Frequently asked questions

### How long does it take?
Minutes to a few hours. Some places keep old data for up to 48 hours.

### Will email stop?
Only if the MX records are missing in the new DNS.

### Can I use nameservers from another company?
Yes. Then manage records there, not here.
"""),

    (C, "DNS records explained: A, AAAA, CNAME, MX and TXT", "Understand the common DNS record types and what each one does for your website, email and domain verification.", True, """DNS records are the lines in your domain's DNS that say where its services live. You manage them in **Domains**, then your domain, then **DNS**.

## A record

Points a name to an IPv4 address, for example your website's server. The name `@` means the domain itself and `www` is the www address.

## AAAA record

The same as A, but for an IPv6 address.

## CNAME record

Makes one name an alias of another name. For example `www` can be an alias of `example.com`. A name with a CNAME cannot have other records.

## MX record

Says where email for the domain is delivered. Each MX record has a priority number; the lowest number is tried first.

## TXT record

Free text used to prove you own the domain and to protect email with SPF, DKIM and DMARC.

## NS and SOA

Nameserver and zone records are managed for you. Do not edit them unless you know what you are doing.

## Frequently asked questions

### What is TTL?
How long, in seconds, other computers keep a record before asking again. A smaller TTL makes changes show faster.

### Can I have two A records?
Yes. Visitors may be sent to either address.

### Why was my change not seen?
DNS changes need time. See [[Why DNS changes take time to spread]].
"""),

    (C, "How to add an A record to point a domain or subdomain", "Add an A record in the DNS page to point your domain, www or a subdomain to a server's IP address, step by step.", False, """An **A record** connects a name to the IP address of a server.

## Steps

1. Open **Domains**, choose your domain and open **DNS**.
2. Press **Add record**.
3. Choose type **A**.
4. In **Name** write `@` for the domain itself, `www` for the www address, or a subdomain such as `shop`.
5. In **Value** write the IPv4 address, for example `203.0.113.10`.
6. Save.

## Check it

Open your domain in a browser from another network. DNS may need a little time. See [[Why DNS changes take time to spread]].

## Common uses

- Pointing the main domain to your hosting.
- Pointing `shop` or `app` to a different server.

## Frequently asked questions

### Where do I find the IP address?
Your DNS page already has an A record for your hosting. Use the same address.

### Should I delete the old A record?
Yes if you want visitors to go only to the new address.

### Can the AI Agent do it?
Yes, after you verify with a security code sent to your email.
"""),

    (C, "How to add a CNAME record", "Create a CNAME record to make a name such as www or shop an alias of another name, with rules and common mistakes.", False, """A **CNAME record** makes one name an alias of another name.

## Steps

1. Open **Domains**, choose your domain and open **DNS**.
2. Press **Add record** and choose **CNAME**.
3. In **Name** write the name to create, for example `www` or `shop`.
4. In **Value** write the target name, for example `example.com`.
5. Save.

## Rules to remember

- A name with a CNAME cannot have any other record.
- The domain itself (`@`) usually cannot be a CNAME.
- The value is a name, not an IP address.

## Frequently asked questions

### CNAME or A record?
Use A for an IP address. Use CNAME to follow another name.

### Why can I not add a CNAME?
The name already has another record. Delete it first.

### Does a CNAME need a dot at the end?
Not in this panel. Write the name normally.
"""),

    (C, "How to add MX records for email", "Add MX records so your domain's email goes to our servers or to another provider such as Google Workspace or Microsoft 365.", False, """**MX records** tell the world which server receives email for your domain.

## To receive email on our servers

When your DNS is here and you create mailboxes, the records are usually set for you. Check the DNS page: there should be an MX record for your domain.

## To use another provider

1. Open **Domains**, choose your domain and open **DNS**.
2. Delete the old MX records.
3. Press **Add record**, choose **MX**, write `@` as the name, the provider's mail server as the value and a priority number.
4. Add each server your provider asks for.
5. Save.

Also switch the domain's mail routing to **remote** in the Email tool so mail is not kept here. See [[How mail routing works: local or remote delivery]].

## Frequently asked questions

### What are the priority numbers?
The server with the lowest number is tried first.

### How long before email works?
Usually minutes, sometimes a few hours.

### Can I use two providers?
Not for the same address. Choose one.
"""),

    (C, "How to add TXT records for SPF, DKIM, DMARC and verification", "Add TXT records to prove you own your domain and improve email delivery with SPF, DKIM and DMARC, step by step.", False, """**TXT records** carry text. They are used to prove ownership to services and to protect your email.

## Add a TXT record

1. Open **Domains**, choose your domain and open **DNS**.
2. Press **Add record** and choose **TXT**.
3. Write the name (`@` or what the service tells you).
4. Paste the value exactly as given.
5. Save.

## SPF

SPF lists which servers may send email for your domain. It is one TXT record on `@`. Only one SPF record may exist.

## DKIM

DKIM signs your email. The provider gives you a name and a long value to add as TXT.

## DMARC

DMARC tells receivers what to do with email that fails SPF or DKIM. It is a TXT record on `_dmarc`.

## Verification records

Google, Microsoft and others ask for a TXT record to prove you own the domain.

## Frequently asked questions

### Do I need all three?
For good delivery, yes. See [[How to stop your email going to spam]].

### Why is the value so long?
DKIM keys are long. Paste it completely.

### Can I add a record from the AI Agent?
Yes, with a security code for safety.
"""),

    (C, "How to add a subdomain", "Create a subdomain such as shop.example.com for a separate website or service on your hosting plan.", False, """A **subdomain** is a prefix on your domain, such as `shop.example.com` or `blog.example.com`.

## Steps

1. Open your hosting plan > **Domains**.
2. Choose to add a **subdomain**, write its name and choose its folder.
3. Save. The subdomain is created and its folder is ready for files.

## Make it a website

Upload files into its folder, or install WordPress there. See [[How to create your first website]].

## Subdomain or new domain?

A subdomain is free and tied to your main domain. A new domain gives a separate identity.

## Frequently asked questions

### Do subdomains count as websites?
They use the same plan resources; website limits depend on how you set them up.

### Do I need DNS for it?
When your DNS is here, the record is created for you.

### Can I delete it later?
Yes, from the same page.
"""),

    (C, "How to set up a domain redirect", "Send visitors from one domain or address to another with a permanent (301) or temporary (302) redirect.", False, """A **redirect** sends visitors from one address to another. It is useful when you changed your domain or want www and non-www to match.

## Steps

1. Open your hosting plan > **Domains**.
2. Choose the domain and press **Redirect**.
3. Write the address to send visitors to.
4. Choose **permanent (301)** if the move is for good, or **temporary (302)** if it will change back.
5. Save.

## 301 or 302?

Use 301 for permanent moves: search engines move your ranking to the new address. Use 302 only for short temporary changes.

## Frequently asked questions

### Will the redirect keep the page path?
Choose the target carefully; redirecting everything to the home page is the simplest.

### How do I remove it?
Open the same page and remove the redirect.

### Is it instant?
Usually yes, but browsers may keep an old redirect for a while.
"""),

    (C, "Why DNS changes take time to spread", "Understand DNS propagation, why a change is not seen everywhere at once, and how to check and speed it up.", False, """After you change DNS, some people see the new website and others still see the old one. This is **DNS propagation**.

## Why it happens

DNS answers are kept (cached) by many computers along the way, for as long as the record's **TTL** says. Until their copy expires, they keep giving the old answer.

## How long?

Most changes are visible in minutes to a few hours. Nameserver changes can take longer, up to 48 hours in rare cases.

## How to check

- Try a different network, such as your phone's mobile data.
- Open a private browser window.
- Restart your router or clear your computer's DNS cache.

## How to speed up the next change

Lower the TTL of the record a day before a planned change, then raise it again afterwards.

## Frequently asked questions

### Can I make it instant?
No. You can only prepare by lowering the TTL in advance.

### Is my website down during propagation?
No. It is served from the old or new place depending on the visitor.

### Does it affect email?
Yes, email may arrive at the old or new provider until the change finishes.
"""),

    (C, "How to renew your domain name", "Renew your domain before it expires: where to find the date, how to pay and what happens if you are late.", False, """A domain must be renewed on time, or it stops working and can be lost.

## Check the date

Open **Domains** and choose your domain. The expiry date is shown. You also get emails before it ends.

## Renew

1. Open the domain and press **Renew**.
2. Choose the years and pay the invoice.
3. The new expiry date is shown after payment.

## If you are late

After expiry a domain enters a grace period where it can still be renewed, sometimes with an extra fee. After that it can be released for anyone to register.

## Frequently asked questions

### Can I renew for several years?
Yes. Choose more years when you renew.

### Does renewal change my DNS or website?
No.

### What if I do not want it anymore?
Simply let it expire after you moved everything away.
"""),

    (C, "How to look up who owns a domain (WHOIS lookup)", "Use the WHOIS lookup to see a domain's registration details, expiry date and nameservers, and learn what privacy protection hides.", False, """A **WHOIS lookup** shows public registration details of a domain.

## Use it

Open the WHOIS lookup from the Domains menu or the Resources menu on the main website, type the domain and press search.

## What you see

- The registrar and the registration and expiry dates.
- The nameservers.
- The status codes, such as locked.
- The owner details, unless privacy protection hides them.

## Why it is useful

Check when a domain expires, whether it is registered, or which nameservers it uses before you transfer it.

## Frequently asked questions

### Why are the owner details hidden?
Privacy protection replaces personal details with a proxy to reduce spam.

### Can I hide my details?
Many extensions allow privacy protection. Ask us for the options for yours.

### Is the information live?
It is fetched from the registry when you search.
"""),
]
