"""Websites and WordPress: signing in, care, updates, speed, staging, copying and deleting."""
C = "websites-wordpress"

ARTICLES = [
    (C, "How to sign in to WordPress without a password", "Open your WordPress dashboard with one click from the customer panel, with no password to remember, and see what to do if the domain does not point yet.", True, """You do not have to remember your WordPress password to reach the dashboard. The panel can sign you in with one click.

## One-click sign in

1. Open **Websites** and find your website.
2. Press **WP Admin**.
3. The WordPress dashboard opens and you are already signed in.

The link works once and for a short time only, so it cannot be reused by someone else.

## If your domain does not point to us yet

When the domain does not lead to your hosting, a normal address would show nothing. In that case WP Admin opens on the **preview address** so you can still work. See [[How to open your website before the domain points here]].

## Next steps

After signing in you can change the theme, write pages and install plugins. If you want your own password, see [[How to set a new WordPress password]].

## Frequently asked questions

### Is one-click sign in safe?
Yes. It needs you to be signed in to your customer panel, and each link is single-use.

### Why does WP Admin show a page about my domain?
Your domain does not lead to this hosting yet. The page explains how to view the site on the preview address and how to point or transfer the domain.

### Can other users sign in this way?
Only people who can sign in to your customer panel and manage the website.
"""),

    (C, "How to set a new WordPress password", "Reset your WordPress administrator password from the customer panel in a few seconds, without email and without losing access.", False, """If you forgot your WordPress password you can set a new one from the panel, even if the WordPress reset email does not arrive.

## Steps

1. Open **Websites** and choose your website.
2. Press **Set a new password**.
3. Write a password with at least 12 characters. Use letters, numbers and symbols.
4. Confirm. The password is sent to WordPress once and is not stored in the panel.

## Choosing a strong password

Use a long phrase that you do not use anywhere else. A password manager helps you create and remember one.

## Sign in without a password

You can always use **WP Admin** in the panel to sign in without typing a password. See [[How to sign in to WordPress without a password]].

## Frequently asked questions

### Does this change my hosting password?
No. Your WordPress password and your hosting password are separate.

### Will other administrators lose access?
No. Only the main administrator's password changes.

### What if I forgot the user name?
Open the website's page in the panel: it shows the administrator user name.
"""),

    (C, "Website care: speed, malware scans, updates and backups in one place", "Website care brings the speed test, malware scan, WordPress updates, maintenance mode, faster pages, traffic and backups together. See what each one does.", True, """**Website care** is the page where you keep a WordPress website healthy. Open **Websites**, choose your website and open **Website care**.

## What you find there

- **Speed**: tests how quickly your home page answers. You get a score, key numbers and short advice, and a button for a detailed Google PageSpeed report.
- **Malware scan**: looks for hidden code, hacker tools and changed WordPress files. Run it now or on a schedule.
- **WordPress updates**: let WordPress install security fixes itself, for WordPress, plugins and themes.
- **Website options**: a maintenance page for visitors, and a request to search engines not to index the site.
- **Faster pages**: compression and caching rules.
- **Traffic**: visitors, page views and top pages.
- **Automatic backups**: copies of your website on the schedule you choose.

## How often should you look?

Look after every big change, and at least once a month. Switch on automatic scans and automatic security updates so most work is done for you.

## Related guides

[[How to speed up your WordPress website]], [[How to scan your website for malware and use quarantine]] and [[How to turn on automatic WordPress updates]].

## Frequently asked questions

### Does Website care work for any website?
It is built for WordPress websites that were created or detected in the panel.

### Will a scan slow my website?
A scan runs on the server for a short time. Choose a quiet time for the schedule.

### Can I undo a change I made there?
Most options can be switched off again in the same place.
"""),

    (C, "How to speed up your WordPress website", "Make a slow WordPress website faster with caching, image compression, fewer plugins, updated PHP and a better plan. Test your speed after every change.", True, """A fast website keeps visitors and ranks better in search engines. These steps work for most WordPress websites.

## 1. Measure first

Open **Website care** and run the **Speed** test. Note the score, the server response time and the page size. Run the Google PageSpeed report for details.

## 2. Switch on Faster pages

**Faster pages** compresses what is sent and lets browsers keep images, styles and scripts. It adds a clearly marked block to your .htaccess file and does not touch your own rules.

## 3. Use a newer PHP version

Open your hosting plan > **PHP** and choose a recent supported version. Newer versions are much faster. See [[How to change your PHP version]].

## 4. Reduce plugins

Every plugin adds work. Remove the ones you do not use and replace heavy ones.

## 5. Optimise images

Resize and compress images before uploading. Large photos are the most common cause of slow pages.

## 6. Keep everything updated

Update WordPress, themes and plugins. See [[How to turn on automatic WordPress updates]].

## 7. Choose the right plan

If the **Resource use** page shows you often reach the CPU or memory limit, move to a bigger plan.

## Frequently asked questions

### What is a good speed score?
Above 80 is good. Above 90 is excellent.

### Does caching break my website?
Rarely. If a page looks outdated after a change, clear the cache or switch off Faster pages to test.

### Is a slow server the problem?
Check "Server response". If it is very high with a light page, look at plugins, PHP and resources first.
"""),

    (C, "How to turn on automatic WordPress updates", "Let WordPress install security fixes and updates for core, plugins and themes by itself, and keep backups so you can roll back safely.", False, """Out-of-date WordPress, plugins and themes are the most common reason websites get hacked. Automatic updates close the gap.

## Choose what updates itself

1. Open **Websites**, choose your website and open **Website care**.
2. Find **WordPress updates**.
3. For **WordPress itself** choose one:
   - Do not update.
   - Security and bug-fix releases (recommended).
   - Every release.
4. Tick **Update plugins automatically** and **Update themes automatically** if you want them updated too.
5. Press **Save**.

## How it works

WordPress looks for updates twice a day when someone visits your website. A plugin update can occasionally change how a page looks, so keep backups switched on.

## Test big updates on staging

For important websites, try updates on a copy first. See [[How to create a staging website to test changes safely]].

## Frequently asked questions

### Are updates free?
Yes for WordPress. Some paid plugins need a valid licence to update.

### What if an update breaks my website?
Restore the latest backup. See [[How to restore a backup]].

### Do updates run when nobody visits?
They run when WordPress is loaded, so a very quiet website may update a little later.
"""),

    (C, "How to use maintenance mode in WordPress", "Show visitors a friendly 'be right back' page while you work on your WordPress website, and keep access for yourself.", False, """Maintenance mode shows visitors a short message instead of your half-finished website.

## Switch it on

1. Open **Websites**, choose your website and open **Website care**.
2. In **Website options** tick **Maintenance mode**.
3. Press **Save**.

Visitors see a "be right back" page. You, signed in to WordPress, still see the real website.

## Switch it off

Untick the box and save. Remember to switch it off after your work.

## When to use it

- Big redesigns.
- Moving to a new theme.
- Large updates.

## Frequently asked questions

### Does it hurt my search ranking?
A short maintenance period is fine. The page tells search engines to come back later. Do not leave it on for days.

### Can I change the message?
The default message is simple. For a custom design use a maintenance plugin.

### Why do I still see my website?
You are signed in to WordPress. Open a private window to see what visitors see.
"""),

    (C, "How to stop search engines indexing a website under construction", "Ask search engines not to index your WordPress website while you build it, and switch it off before launch so people can find you.", False, """While you build a website you may not want it to appear in Google yet.

## Switch the request on

1. Open **Websites**, choose your website and open **Website care**.
2. In **Website options** tick **Ask search engines not to index this website**.
3. Press **Save**.

## Remember to switch it off

Before you launch, untick the same box. If you forget, your website will not appear in search results.

## This is a request

The setting asks search engines to stay away. Well-behaved search engines respect it, but it is not a password. To fully hide a website use a password-protected folder. See [[How to protect a folder with a password]].

## Frequently asked questions

### How long until Google removes my pages after I switch it on?
It can take days. Pages not yet indexed stay out.

### How do I get indexed after launch?
Switch the option off, publish a sitemap and be patient.

### Does staging use this setting?
Yes. A staging website asks search engines not to index it.
"""),

    (C, "How to create a staging website to test changes safely", "Make a private copy of your WordPress website, test updates and design changes there, and publish to live only when you are happy.", True, """A **staging website** is a private copy of your WordPress website on `staging.yourdomain`. Nothing you do there changes your live website until you publish it.

## Create a staging website

1. Open **Websites**, choose your WordPress website and open **Staging website**.
2. Press **Create staging website**.
3. We copy the files and the database into a new folder with its own database. A large website can take a few minutes; the page updates by itself.
4. Open the staging address and make your changes.

## Publish to live

1. Open the staging page and read the warning.
2. Type **PUBLISH** to confirm.
3. A backup of the live website is taken first where the server can make one.

Posts, orders and comments made on the live website after the copy was made are replaced. Publish soon after testing.

## Delete the staging website

**Delete staging website** removes its files, database and subdomain. Your live website is not touched.

## Frequently asked questions

### Can search engines see staging?
It asks them not to index it.

### Do I get one staging site per website?
Yes. Each website has one staging copy at a time.

### What if publishing fails?
Your live website is restored from the safety backup where one was made, and the page shows the reason.
"""),

    (C, "How to copy a website to another domain", "Copy a WordPress website with its files and database onto another domain of your hosting plan to start a second site from the first.", False, """You can copy a WordPress website onto another domain of your plan. The files and the database are copied and the addresses are changed to the new domain. The original website does not change.

## Before you start

- Add the new domain to your plan. See [[How to add another website to your hosting plan]].
- Its folder must be empty.

## Copy the website

1. Open **Websites** and choose the WordPress website to copy from.
2. Open **Copy website**.
3. Choose the domain to copy to and press **Copy website**.
4. A progress page shows how it is going. When it says ready, open the new website.

## What if it fails?

Nothing half-finished is left behind: the new folder and database are removed and the page shows the reason.

## Frequently asked questions

### Are search settings copied?
The copy keeps the original's setting for search engines.

### Can I copy to a domain not on my plan?
No. First add the domain to your plan.

### Does it copy email?
No, only the website's files and database.
"""),

    (C, "How to add another website to your hosting plan", "Host a second domain on the same hosting plan: add the domain, set up WordPress or a custom site and point its nameservers.", False, """Many plans can hold more than one website. Each website has its own domain and its own folder.

## Add the website

1. Open **Websites** and choose your hosting plan.
2. Press **Add a website** and type the domain.
3. Set the website up: choose **WordPress** or **Custom website**.
4. Point the domain's nameservers to us. See [[How to point your domain to our hosting]].

## How many can I add?

The page shows how many websites your plan allows and how many are used. If all are used, move to a bigger plan.

## What each website gets

Its own folder, its own WordPress (if you choose it), its own Website care, staging and backups.

## Frequently asked questions

### Do all websites share resources?
Yes. They share the plan's disk space, bandwidth and CPU.

### Can I remove a website later?
Yes. The domain is taken off the plan and the free place is available again.

### Can I use a subdomain instead?
Yes, subdomains of your domains can be added in the Domains tool.
"""),

    (C, "How to delete a website safely", "Delete a website from your hosting plan without losing the plan or the domain, with the steps, a backup reminder and what is removed.", False, """Deleting a website removes its files and the database we made for it. Your **hosting plan and the domain stay**, and you can create a new website at any time.

## Before you delete

Create a backup if you may need anything later. The deletion cannot be undone. See [[How to create and download a backup]].

## Steps

1. Open **Websites**, choose the website and press **Delete website**.
2. Read what will be removed: the files, the staging copy and the database.
3. Type the website's address exactly to confirm.

## For a website on an added domain

The domain is also taken off the plan, which makes a place free for another website.

## Frequently asked questions

### Is email deleted too?
No. Mailboxes of the domain are not part of the website.

### Will my domain expire?
No. Deleting the website does not affect the domain registration.

### Can I recover it?
Only from a backup you made earlier.
"""),

    (C, "How to manage WordPress plugins and themes from the panel", "List, update, switch on or off and remove WordPress plugins and themes from the customer panel, even when you cannot sign in to WordPress.", False, """The **Plugins and themes** manager lets you control your WordPress add-ons from the panel. It is very useful when a plugin breaks your website and you cannot sign in.

## Open it

Open **Websites**, choose your website, open **Website care** and press **Plugins and themes**. Press the load button to read the list from your website.

## What you can do

- See each plugin and theme, its version and whether an update is waiting.
- **Update** one add-on.
- **Switch off** a plugin that causes trouble, and switch it on again.
- **Delete** a plugin or theme you do not use.

## When a plugin breaks your site

1. Open the manager.
2. Switch off the plugin you changed last.
3. Reload your website.

## Frequently asked questions

### Will switching off a plugin delete its data?
No. Its settings stay until you delete the plugin.

### Can I install a new plugin here?
Install new plugins in the WordPress dashboard.

### Why is the list empty?
Press the load button first. The list is read from your website when you ask for it.
"""),

    (C, "What happens during a WordPress installation", "Understand each step of the automatic WordPress installation: database, download, check, unpacking, settings and final touches.", False, """When you create a WordPress website the progress page shows each step. Here is what each one does.

## The steps

1. **Database and its user created**: a private database and user are made for your website.
2. **Downloading WordPress**: the official WordPress package is downloaded.
3. **Checking the download is genuine**: its checksum is verified so the files are the real ones.
4. **Unpacking the WordPress files**: the files are placed in your website's folder.
5. **Writing the website's settings**: the settings file is created with your database details.
6. **Setting up the website and the administrator**: your site name and administrator are created.
7. **Final touches**: security keys, permalinks and the preview address are set.

## If something fails

The installer removes what it made: files, database and user. Nothing half-done is left. The page shows the reason so you can try again.

## Frequently asked questions

### Can I leave the page?
Yes. The installation continues on the server.

### Where is my password stored?
Not in the panel. It is sent once to WordPress.

### Can I install again?
Yes. Delete the failed website first or follow the message shown.
"""),
]
