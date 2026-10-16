"""Longer sections added before the questions of the getting started articles."""

EXTRAS = {
    "A tour of your customer panel": """## Tips for getting around faster

Bookmark the pages you use every week, such as your hosting plan and Billing. Use the browser's back button freely: the panel does not lose your place. When you are not sure where something is, type its name in the search box instead of clicking through menus.

## What you will not see

The panel shows only what your account can use. If a tool is missing, your plan or server may not offer it yet, or your role may not allow it. The page explains this instead of showing an error, and you can always ask the AI Agent or open a ticket.""",

    "How to create your first website": """## Tips before you start

Decide the website's purpose first: a blog, a company page or a shop. It makes choosing a theme and plugins much easier. Write down the administrator user name and password in a password manager right away, because the password is not shown again.

## After the website is ready

Change the theme and the site title, delete the sample posts, create your main pages and set the permalinks. Then run [[Launch checklist: what to do after your website is ready]] before you announce the website.""",

    "How to point your domain to our hosting": """## Common mistakes

Typing only one nameserver, adding spaces or an extra dot, or changing nameservers while email still lives at the old place are the usual mistakes. Copy both nameservers exactly as shown and check your email records first.

## What to prepare

Before you switch, list the services that use your domain: website, email, subdomains and verification records. Make sure each one exists in the new DNS. The more complete the new DNS is, the smaller the gap during the switch.""",

    "How to open your website before the domain points here": """## Using the preview well

Treat the preview as your private workshop. Build the pages, set the menus and test the forms there. Do not share the preview link in public places, and do not test payments with real cards before your domain and SSL are ready.

## Moving to the real address

When the nameservers lead to us, the website opens on your own domain with the same content. Switch on SSL and Force HTTPS, then check that images and links use your real address.""",

    "Hosting plan basics: disk space, bandwidth and resources explained": """## How much do I really need?

A small WordPress website with a few pages needs a few hundred megabytes. Image-heavy sites, shops and sites with many backups need much more. Email also counts towards disk space, so large mailboxes can fill a plan.

## Keeping usage low

Compress images before uploading, delete themes and plugins you do not use, empty the Trash in the File manager and download old backups to your computer instead of keeping many on the server.""",

    "What is cPanel and how do you use its tools here?": """## Why a separate hosting password exists

cPanel has its own sign-in so that the hosting layer is separate from your customer account. A leak of one does not open the other. Most customers never need it, but tools such as FTP and SSH use it.

## When to ask for more

If you need a feature that is not in the panel, tell us what you want to do rather than which tool you want. Often there is a simple way, and we can tell you if something has to be done by our team.""",

    "WordPress or a custom website: which should you choose?": """## Cost and time

WordPress gets you online in minutes and has a huge library of free themes and plugins. A custom website costs more developer time, but can be lighter and exactly what you need. Think about who will update the content after launch.

## Growing later

Starting with WordPress rarely closes doors: you can add a shop, memberships or a blog later. A custom website can still be placed in its own folder next to WordPress on another domain of your plan.""",

    "How to find your hosting details": """## Information developers ask for

Besides the domain, username and server, a developer may need the PHP version, the database name and the FTP host. The PHP page shows the version and each database is listed under Databases. Send these in a ticket or message, but never the password.

## Keep a record

Write the details in a safe note: the plan name, renewal date, domain registrar and where your DNS is managed. It saves time if you ever need to move or recover your website.""",

    "Launch checklist: what to do after your website is ready": """## Test as a visitor

Open the website in a private window on your phone and your computer. Click every menu item, submit every form and check that the emails arrive. Test the speed from [[Website care: speed, malware scans, updates and backups in one place]].

## After you launch

Submit your sitemap to search engines, watch your traffic page for the first week and keep your backups on. Plan a monthly routine: updates, a scan and a check that forms still work.""",

    "How to use the search and shortcuts in the panel": """## Search tips

Search matches parts of words, so "inv" finds invoices and "ssl" finds your SSL pages. Search also finds your domains and websites by name, which is the quickest way to open a particular one when you have many.

## On a phone

Use the menu button for the main areas and the search box at the top for everything else. The AI Agent button is on every page if you prefer to ask in your own words.""",

    "How to see your plan's renewal date and what is included": """## Avoid surprises

Add the renewal dates of your hosting and domains to your calendar a week ahead. Pay the invoice early, or report a bank transfer early, so nothing is suspended while you wait for the money to arrive.

## Comparing plans

Before you upgrade, note which limit you hit: disk space, bandwidth, number of websites or CPU. Choose the plan that raises that limit, not just the most expensive one.""",

    "Hosting and domain terms explained: a simple glossary": """## More words you may meet

- **Uptime**: the share of time your website is reachable.
- **Caching**: keeping a ready copy of a page so it loads faster.
- **CDN**: a network of servers that delivers your files from a place near the visitor.
- **Backup**: a copy you can restore.
- **Staging**: a private copy for testing changes.

## How to learn more

Every term here has a guide in this help centre. Search for the word, or ask the AI Agent to explain it in English, Urdu or Roman Urdu.""",
}
