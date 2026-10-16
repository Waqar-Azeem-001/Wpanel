"""Longer sections for the website, WordPress, domain and DNS articles."""

EXTRAS = {
    # ---- websites and WordPress -------------------------------------------------------------------------------------------------------------
    "How to sign in to WordPress without a password": """## Why this is safer than a saved password

A one-time link cannot be reused, leaked in a browser's password list or guessed. You still need to be signed in to your customer panel, so protect that account with a strong password.

## Team members

Give team members their own WordPress users with the role they need (editor, author) instead of sharing the administrator. They can still be helped from the panel if they lock themselves out.""",

    "How to set a new WordPress password": """## Good passwords for WordPress

Use at least 12 characters and avoid names, dates and words that appear on your website. A password manager can generate and store one. Never reuse your hosting or email password.

## After changing it

Sign out of other devices, update any app that signs in to WordPress, such as a mobile app or a backup plugin, and check that the administrator email address is correct for future reset emails.""",

    "Website care: speed, malware scans, updates and backups in one place": """## A simple monthly routine

1. Run a speed test and note the score.
2. Run a malware scan.
3. Look at the list of updates and apply them.
4. Check that your latest backup exists and download a copy.

Fifteen minutes a month prevents most big problems.

## Read the results calmly

A yellow or red note is advice, not an emergency. Fix the biggest item first and test again. If you are unsure what a message means, ask the AI Agent or open a ticket with a screenshot.""",

    "How to speed up your WordPress website": """## The order that works best

Fix the cheap, big things first: image sizes, unused plugins and caching. Only then consider a bigger plan. Measuring after each change tells you what helped, so change one thing at a time.

## What to avoid

Avoid installing several caching or optimisation plugins together: they conflict. Avoid auto-playing video, heavy sliders and many external fonts. Every third-party script you add is a possible delay.""",

    "How to turn on automatic WordPress updates": """## A sensible setup

For most sites: security and bug-fix releases for WordPress itself, plus automatic updates for plugins and themes you trust. Keep backups on. For a shop or a membership site, test major updates on a staging copy first.

## What automatic updates do not do

They do not remove plugins you no longer use, and they cannot update a paid plugin whose licence ran out. Review the list in the Plugins and themes manager from time to time.""",

    "How to use maintenance mode in WordPress": """## Plan a short window

Switch maintenance mode on only for the time you need, announce the work on social media if you have regular visitors, and test everything before switching it off.

## Better for big changes

For redesigns, build the new version on a staging website and publish it in one step instead of showing a maintenance page for hours.""",

    "How to stop search engines indexing a website under construction": """## Why it matters

Google may show half-finished pages with placeholder text. That is bad for your first impression and hard to correct. Asking search engines to stay away until you are ready keeps your first appearance clean.

## Launch day

Before you announce the website, switch the request off, check your page titles and descriptions, and submit your sitemap. It can still take days or weeks for pages to appear.""",

    "How to create a staging website to test changes safely": """## What to test on staging

New themes and plugins, major WordPress updates, PHP version changes, new checkout flows and any custom code. Click through the important paths: login, forms, checkout and search.

## Keep staging in step

A staging copy is a snapshot. If your live site changes a lot, create a fresh staging copy before your next test, and publish soon after you finish so you do not overwrite newer live content.""",

    "How to copy a website to another domain": """## Good uses

Start a second site from a proven design, build a language version of a website or make a test site on a different domain. After copying, change the site title, the pages and the settings that should differ.

## After the copy

Check the new site's SSL, permalinks, email settings and search engine setting. Remember that a copy shares the plan's disk space and resources with the original.""",

    "How to add another website to your hosting plan": """## Plan your resources

Several websites share one plan's disk space, bandwidth and CPU. A busy site can slow the others, so watch the resource page after adding a website. Give each website its own backups and updates.

## Domains you already own

If the new domain is registered elsewhere, point its nameservers to us after you add it. See [[How to point your domain to our hosting]]. Add its email records at the same time so nothing breaks.""",

    "How to delete a website safely": """## A checklist before deleting

Download a backup, check that no other site uses its database, remove DNS records you no longer need and tell your visitors if the address will stop working. Delete only what you are sure about.

## After deleting

Check your disk usage and the plan page: the freed space and the free website place are available again. If you deleted by mistake, restore the backup into a new website.""",

    "How to manage WordPress plugins and themes from the panel": """## Healthy plugin habits

Install plugins from the official directory or trusted sellers, delete the ones you do not use and keep the rest updated. Two plugins doing the same job usually cause slowness or conflicts.

## When something breaks

Switch off the last plugin you touched first. If the website returns, update that plugin, find an alternative or ask its author for help. Keep a note of what you changed and when.""",

    "What happens during a WordPress installation": """## Why the steps matter

Each step is a safe checkpoint. If one fails, the installer stops and removes what it made, so a failed attempt never leaves a half-built website that blocks the next try.

## After a successful install

Sign in with WP Admin, update the theme, set the permalinks and delete the sample content. Then follow [[Launch checklist: what to do after your website is ready]].""",

    # ---- domains and DNS ---------------------------------------------------------------------------------------------------------------------
    "How to register a domain name": """## Choose carefully

Pick a name that is easy to say, spell and remember. Avoid trademarks of others. A .com is the safest default; a local ending can help a local business. Register the common misspellings only if they matter to you.

## Protect it

Keep the registrant email address current, switch on the transfer lock and renew in time. Losing a domain means losing the website and email on it.""",

    "How to transfer a domain to us": """## Prepare before you transfer

Reduce the risk of downtime by copying your DNS records before the transfer and keeping your nameservers unchanged until it completes. Check the domain's expiry date: transfer well before it ends.

## After the transfer

Confirm the domain appears in your panel, the lock is on and the contact details are right. Then decide whether to move the website and email as well.""",

    "How to lock or unlock a domain and move it away": """## Why locks exist

Domain theft usually starts with an unlocked domain and a stolen transfer code. The lock and the identity check on codes are your protection. Unlock only for a planned transfer and lock it again afterwards if it stays with us.

## Moving with care

Tell your new company when you start, keep your website running during the transfer and do not change nameservers until it finishes.""",

    "How to change nameservers": """## A safe way to change

Write down your current records, add them in the new DNS, lower the TTL a day earlier and switch the nameservers at a quiet time. Check the website and email right after.

## If something breaks

Switch the nameservers back to the old values and wait. The old DNS comes back the same way, which is why you keep a copy of the records first.""",

    "DNS records explained: A, AAAA, CNAME, MX and TXT": """## How records work together

A visitor typing your domain triggers an A record lookup for the website. A sender of email triggers an MX lookup for mail. A mail server checking spam reads your TXT records. One domain uses all of them at once.

## Good habits

Change one record at a time, write down the old value before you edit and keep your TTL at a moderate value such as 3600 seconds. Remove records you no longer use.""",

    "How to add an A record to point a domain or subdomain": """## Typical examples

- `@` to your hosting's address for the main website.
- `www` to the same address, or a CNAME to `@`.
- `shop` to an online shop platform's address.
- `mail` to your mail server's address.

## Mistakes to avoid

Do not point `@` at two different services. Do not delete the A record your website depends on before you add the new one. Double-check the IP address digit by digit.""",

    "How to add a CNAME record": """## Typical examples

- `www` as an alias of the main domain.
- `shop` as an alias of a shop platform's address.
- `status` as an alias of a status page service.
- `autodiscover` for some mail programs, if your mail provider tells you.

## Remember

Only one CNAME per name, and no other record can share that name. If you also need email on the same name, use a different name.""",

    "How to add MX records for email": """## Move email safely

Before you switch MX records, create the mailboxes at the new provider and copy the old mail across. After the switch, mail takes a short time to start arriving in the new place, so keep the old mailboxes for a few days.

## Check afterwards

Send a test message from another address, then reply from the new mailbox. Also test mail from a free provider to see that SPF and DKIM pass.""",

    "How to add TXT records for SPF, DKIM, DMARC and verification": """## One record per purpose

Keep one SPF record only. If you use several senders, combine them in the same record. Add DKIM exactly as given and start DMARC with a monitoring policy, then tighten it later when reports look clean.

## Typical SPF

A simple SPF record lists your mail server and your newsletter service, then ends with a rule for everything else. Your email provider gives you the exact text; do not guess it.""",

    "How to add a subdomain": """## Uses

Use subdomains for a shop, a blog, a staging site, a customer portal or a separate app. They keep services apart but under your brand.

## SEO note

Search engines treat a subdomain as a separate site in many cases. A blog in a folder of your main site often helps the main site's ranking more than a blog on a subdomain.""",

    "How to set up a domain redirect": """## Typical redirects

From the old domain to the new one, from `http` to `https`, from non-www to www or the reverse. Choose one main version of your address and redirect all others to it.

## Check it

Open the old address in a private window and make sure you land where you expect. Test the pages visitors bookmarked, not only the home page.""",

    "Why DNS changes take time to spread": """## A practical plan for a move

One day before: lower the TTL. At the change: update the record. During the next hours: test from different networks. Next day: raise the TTL again.

## What visitors notice

Some see the old site, some the new one, for a few hours. Keep both working during that time. If the old site must stop, wait until the change has spread.""",

    "How to renew your domain name": """## Why auto-renewal is not always on

Cards expire and emails get missed. Do not rely on one reminder. Put the date in your calendar and check the Domains page now and then.

## Longer is cheaper and safer

Renewing for several years reduces the work and signals stability. It also protects you if you are away when a reminder arrives.""",

    "How to look up who owns a domain (WHOIS lookup)": """## What to look at

Check the registrar, the expiry date, the status and the nameservers. If you want to buy a name that is taken, the expiry date tells you when it may become free.

## Respect privacy

Owner details are often hidden. If you need to contact the owner, use the contact method given in the record rather than looking for personal information.""",
}
