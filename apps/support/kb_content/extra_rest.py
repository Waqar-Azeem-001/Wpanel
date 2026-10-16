"""Longer sections for security, troubleshooting, billing and account articles."""

EXTRAS = {
    # ---- security and backups ---------------------------------------------------------------------------------------------------------------
    "How to get a free SSL certificate and switch on HTTPS": """## Why HTTPS matters

Browsers mark sites without HTTPS as not secure, which scares visitors. Search engines also prefer HTTPS pages, and forms and logins must be encrypted to protect your visitors' data.

## After switching it on

Check that images and scripts load over https too, update links in your content and tell search engines about the https version by keeping one redirect from http to https. See [[How to fix mixed content warnings after switching to HTTPS]] if the padlock warns.""",

    "How to create and download a backup": """## The 3-2-1 idea

Keep three copies of important data, on two kinds of storage, with one copy away from the server. A backup on your computer and another in cloud storage, plus the one on the server, covers most accidents.

## Name and store them

Add the date to the file name, keep a few generations and delete very old ones. Store backups privately: they contain your database and settings.""",

    "How to restore a backup": """## Choosing the right backup

Pick the most recent backup from before the problem started. If you do not know when it began, restore a staging copy first and check it.

## After you restore

Clear caches, run a malware scan if the cause was a hack, change passwords and update everything. Then create a new backup of the healthy state.""",

    "How to set up automatic backups": """## How often?

A site that changes daily (a shop, a busy blog) needs daily backups. A site that rarely changes is fine with weekly. Always make a manual backup before updates and big edits.

## Test your backups

A backup you never tested may not work when you need it. From time to time download one, or restore it to a staging copy, and check it opens.""",

    "How to scan your website for malware and use quarantine": """## Understanding the result

A scan lists the file, the rule that matched and a short reason. Not every match is dangerous: some legitimate plugins use code that looks suspicious. Compare against your plugin list before deleting anything permanently.

## After cleaning

Reinstall WordPress core and plugins from trusted sources, change all passwords and keep automatic scans on so a repeat attack is noticed within a day.""",

    "How to protect a folder with a password": """## When it is the right tool

For a private download area, a client preview, a staging copy or an admin tool. It is not a replacement for proper user accounts on a public website.

## Share credentials safely

Send the user name and password in separate messages through a private channel and change the password when the work is finished.""",

    "How to check that your files have not been changed (integrity check)": """## Reading the list

Changed core files you did not touch are a red flag. New files in upload folders with names that look random are another. Changed theme files after you edited them are normal.

## Make it a habit

Take a baseline after a clean update, check once a month and after any suspicious event. Combine it with malware scans and backups for layered protection.""",

    "How to keep WordPress secure: a practical checklist": """## Think in layers

No single step makes a website safe. Updates remove known holes, strong passwords stop guessing, backups let you recover, scans notice problems and HTTPS protects traffic. Each layer covers the weakness of another.

## Review every quarter

Remove unused users, check administrator accounts, remove old plugins and read your scan results. Security is a routine, not a one-time job.""",

    "What to do if your website is hacked": """## Find the way in

Look at the files changed recently, new administrator users, plugins you did not install and the dates of the first problem. Often the cause is one outdated plugin or a stolen password. Fix the cause, or it will happen again.

## Tell people if needed

If customer data may have been exposed, check your legal duties and tell the people affected. Keep notes of what happened and what you did.""",

    "How to create strong passwords and keep them safe": """## Use a password manager

It creates long random passwords, fills them in for you and warns about reuse. You remember one strong master passphrase instead of dozens of weak ones.

## Add a second step where possible

Two-step sign-in on your email and any account that offers it adds a lot of protection. Your email is the most important: it can reset almost everything else.""",

    # ---- troubleshooting ----------------------------------------------------------------------------------------------------------------------
    "How to fix 'Error establishing a database connection' in WordPress": """## Quick checks first

Open your hosting plan's Databases page to see that the database exists. Open Resource use to see if the server was overloaded. Many "database errors" are short and disappear on reload.

## If you just moved the website

After a move the database host, name, user and password often differ. Compare each value in wp-config.php with the Databases page, character by character.""",

    "How to fix the WordPress white screen of death": """## Turn on debugging briefly

Developers can switch on WordPress debug logging to write the exact error to a file. Do it for a short time, read the file, then switch it off: it should not stay on a live site.

## Memory is the second suspect

If the log names no plugin, raise the memory limit and try again. A page builder with many widgets can need much more memory than a simple theme.""",

    "How to fix a 500 Internal Server Error": """## Work from the newest change

Ask: what did I change just before it started? A new plugin, an update, a new rule in .htaccess, a PHP version change. Undo that change first. Most 500 errors disappear within minutes this way.

## Keep a log of changes

Writing down each change with the date turns a mystery into a simple search. It also helps us help you faster.""",

    "How to fix 404 Not Found errors and broken permalinks": """## Check the basics

Is the page published? Is its address spelled the same as the link? Is the link in a menu pointing to an old address? A single wrong character causes a 404.

## For many missing pages

Resave the permalinks, then check that the .htaccess file contains the WordPress block. If you use a caching plugin, clear its cache.""",

    "How to fix a 403 Forbidden error": """## Permissions in plain words

Permissions say who may read, write and run a file. Folders usually 755 and files 644 are correct. A folder set to 000 or 700 may refuse the web server. Set them back and reload.

## Security plugins

Some security plugins block countries, addresses or whole folders. If the error only appears for some visitors, review the plugin's block lists.""",

    "How to fix a 503 Service Unavailable error": """## Spot the pattern

Does it happen at the same time every day, after you publish, or after a visitor spike? A pattern points to a scheduled task, a heavy page or an automated crawler.

## Reduce the load

Cache your pages, limit heavy admin tasks to quiet hours, and block abusive addresses. If real traffic keeps hitting the limit, that is good news: the website needs a bigger plan.""",

    "How to fix ERR_TOO_MANY_REDIRECTS (redirect loop)": """## Test in steps

Clear cookies, test in a private window, then switch off plugins one by one. If the loop stops after switching one off, review its redirect settings.

## Keep one source of truth

Decide where the https redirect is done: in WordPress, in .htaccess or in a CDN. Do it in one place only. Two places doing the same job is the usual cause of loops.""",

    "How to fix mixed content warnings after switching to HTTPS": """## Where the old addresses hide

In post content, widget text, theme options, page builder settings and custom CSS. A search-and-replace plugin can update the database in one step. Always create a backup before replacing.

## Test again

Open several pages, including the home page, a post and the checkout. Look at the browser console for new warnings after you change something.""",

    "How to fix SSL and 'Not secure' warnings": """## Check each name

A certificate covers specific names. A certificate for `example.com` does not cover `shop.example.com` unless it was requested for it. Request the certificate again including every name visitors use.

## DNS first

The request fails when the domain does not lead to this hosting. Fix DNS first, wait, then request the certificate again. Do not keep pressing the button: wait a few minutes between tries.""",

    "How to fix a slow website: a step-by-step checklist": """## Think like a visitor

Open the site on a mid-range phone with mobile data. The first screen should appear in two or three seconds. If it does not, look at the biggest items: the home page image, fonts and scripts.

## Re-test

After each change run the speed test again and write down the score. Changes that do not help should be undone so the website does not get more complicated.""",

    "How to fix 'DNS_PROBE_FINISHED_NXDOMAIN' and a domain that does not open": """## Use outside help

Check the domain on a public DNS checking website from another network. If it resolves there but not on your computer, flush your computer's DNS cache or restart the router.

## If the domain expired

An expired domain stops resolving. Renew it quickly: during the grace period the same address often comes back, and the sooner you renew the less downtime you have.""",

    "How to get back into WordPress if you are locked out": """## Prevent lock-outs

Keep an administrator email address that works, save your password in a manager, and do not run two security plugins at once. Keep one extra administrator account for emergencies.

## Check for intruders

If you cannot sign in and did not change anything, someone else may have. Look at the users list when you are back in and remove unknown administrators.""",

    "How to fix email bounce messages such as 550 and 5.7.1": """## Read the human part

Bounce messages contain a code and a sentence. Ignore the code at first and read the sentence: it says "user unknown", "mailbox full", "blocked because of spam" or "SPF failed". That sentence is the fix.

## Do not keep resending

Repeating a message that bounced for a spam reason can worsen your reputation. Fix the cause, then send one test message.""",

    "How to fix WordPress upload errors and 'file exceeds the maximum upload size'": """## Make files smaller too

Even with higher limits, huge images slow your site. Resize photos to the size they appear at, compress them and use modern formats where possible.

## Large themes and imports

For a big theme or import file, upload it with FTP or the File manager and install from the folder. This avoids web upload limits and timeouts.""",

    "How to fix 'Briefly unavailable for scheduled maintenance'": """## Why it sticks

WordPress creates the .maintenance file at the start of an update and removes it at the end. If the update stops (a closed tab, a timeout, low memory), the file stays behind.

## Prevent it

Update one thing at a time, keep memory limits sensible and do not close the page during updates. Automatic updates help because they run in the background.""",

    # ---- billing and orders -------------------------------------------------------------------------------------------------------------------
    "How to pay an invoice online": """## Make payment smooth

Have your card ready, check that online payments are enabled for it and use the same name as on your billing details. If your bank asks for an extra approval, finish it quickly in the bank's app.

## Keep your records

Download the receipt and save it with your accounts. The invoice stays in Billing whenever you need it.""",

    "How to report a bank transfer payment": """## Help us match it

Put the invoice number in the transfer's reference. Use the same name as on your account. Add the amount exactly. These three details let us match a payment within minutes of seeing it.

## International transfers

Bank fees can reduce the amount received. If a small difference remains, tell us in the form so we can approve the invoice.""",

    "Payment methods you can use": """## Choosing a method

Use an online method for instant activation. Use bank transfer if you pay from a company account or prefer it; report it so we can match it fast.

## Business customers

Check that your billing details show the company name and tax number if you need them on invoices, before you pay.""",

    "How to renew your hosting plan": """## Renew with a plan

Renew a few days before the due date to avoid any gap. A longer renewal period reduces the work and is often better value. Keep your backups up to date before big changes in the renewal period.

## Review your needs

Renewal is a good moment to look at resource use and decide if you need a different plan.""",

    "How to understand your invoice": """## Check before you pay

Look at the period each item covers, the quantity and the price. Check the billing name and address. If something is wrong, open a ticket before paying so we can correct it.

## Keeping records

Download each paid invoice as a PDF. They are your proof of payment for accounts and taxes.""",

    "Order status explained: pending, active, failed and cancelled": """## What helps us help you

Give us the order number from the Orders page and the invoice number. Tell us what you expected: for example "hosting for example.com". It speeds up every check.

## Do not order twice

If an order looks stuck, do not place a second one. Ask us first, so you are not charged twice.""",

    "How to upgrade or change your hosting plan": """## Think about the whole plan

A bigger plan raises limits but does not fix a heavy plugin. Check Resource use and the speed test first. If only one limit is the problem, ask us which plan raises that limit.

## After you move

Test your website, email, SSL and cron jobs on the new plan, then let the old plan end.""",

    "What happens if an invoice is not paid": """## Do not wait for suspension

If you cannot pay on time, tell us before the due date. We can often help with a short extension. After suspension, bring the service back by paying the overdue invoice.

## Protect your data

Keep your own backups so a billing problem never puts your data at risk.""",

    "Refunds and cancellation policy explained": """## Before you cancel

Download your backups and move your email and domains if you want to keep them. A cancelled service can be removed after a short period.

## Be specific in a refund request

Include the invoice number, the date, the reason and what you would like. Clear requests are answered faster.""",

    "How the Refer and earn programme works": """## Share smartly

Share your link where people already look for hosting or domains: your website, a blog post or a community you belong to. Explain why you use us; honest recommendations convert better.

## Keep it fair

Follow the programme rules: no self-referrals and no misleading promises. The rules are shown on the programme page.""",

    # ---- account and support ----------------------------------------------------------------------------------------------------------------
    "How to open a support ticket": """## Choose the department

Technical for websites, email and DNS. Billing for invoices and payments. Sales for questions before you buy. General for everything else. The right department gets you to the right person faster.

## Keep one thread

Reply on the same ticket instead of opening new ones, so our team sees the full history.""",

    "How to write a good support ticket": """## Make it easy to reproduce

Write the steps you took: "I opened Website care, pressed Scan now, and the page showed a red error". Someone can repeat that and see the problem themselves.

## Say how urgent it is

A short line such as "my shop is offline" tells us the priority. Keep the details in the message, not only in the subject.""",

    "How to use the AI Agent": """## Get better answers

Write the domain name and what you want to do, for example "check SSL for example.com". Ask one thing at a time. If an answer is not clear, say "samajh nahi aya" and the Agent will do more.

## When to choose a person

For billing disputes, refunds, suspension and anything the Agent says it cannot do, press Talk to a person.""",

    "How to talk to a person or ask for a callback": """## Choose the right channel

Live chat is best for quick questions. A ticket is best for longer problems with details and files. A callback is best when talking is easier than writing.

## Be ready for the call

Have your domain name and the problem in front of you. Never tell anyone your passwords or security codes on a call.""",

    "How to change your hosting password": """## After you change it

Update FTP programs, backup tools and any script that connects with the old password. If a service starts failing with "login incorrect", the old password is still saved there.

## Make it strong

Use a long phrase or a random password from your manager, and never reuse it elsewhere.""",

    "How to update your profile and billing details": """## Keep contacts current

Use an email address you read daily. Add a phone number we can use for important matters. If you use a company, write its legal name and tax number so invoices are valid for your accounts.

## Check after a change

Open an upcoming invoice and see that the new details appear. Ask us if an older invoice needs a correction.""",

    "How to choose which notifications you receive": """## Recommended settings

Keep billing, security and service messages on. Switch off marketing-style messages if they are not useful. Keep the in-app bell on so you see notices even if an email is missed.

## Check your spam folder

If you expect an email and it does not arrive, look in Spam and add our address to your contacts.""",

    "Keep your account safe: passwords, codes and phishing": """## Security habits

Use a different password for your customer account than for your email, turn on two-step sign-in on your email, and check your account's sign-in history if you suspect misuse.

## Report suspicious messages

If you receive a message pretending to be us and asking for your password or code, do not respond. Forward it in a ticket so we can warn others.""",

    "Our policies: terms, acceptable use, refunds and privacy": """## Read before you build

The acceptable use policy matters most for what you host: spam, malware and illegal content are not allowed. Knowing the rules protects your website from sudden suspension.

## Ask when unsure

If you are not sure that your project is allowed, ask in a ticket before you build it. We would rather answer a question than suspend a service.""",
}
