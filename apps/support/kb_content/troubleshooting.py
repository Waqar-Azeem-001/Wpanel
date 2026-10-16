"""Troubleshooting: the errors people search for, with the cause and the fix."""
C = "troubleshooting"

ARTICLES = [
    (C, "How to fix 'Error establishing a database connection' in WordPress", "Fix the WordPress error 'Error establishing a database connection' by checking the database, user, password and settings file.", True, """This message means WordPress cannot reach its database.

## Common causes

- The database name, user or password in the settings file is wrong.
- The database user lost access to the database.
- The database server was busy for a moment.
- The website was moved or restored and the details changed.

## How to fix it

1. Open your hosting plan > **Databases** and check the database and its user still exist and the user has **access**. See [[How to create a database and a database user]].
2. Open the website's folder in the **File manager** and look at `wp-config.php`. The values for `DB_NAME`, `DB_USER` and `DB_PASSWORD` must match.
3. If you do not know the password, set a new one for the database user and write it into `wp-config.php`.
4. Reload your website.

## If it is only sometimes

Check **Resource use**. Heavy traffic can overload the database. See [[How to check your resource usage (CPU, memory and processes)]].

## Frequently asked questions

### Did I lose my content?
Usually not. The data is in the database; WordPress just cannot reach it.

### Can the AI Agent fix it?
It can read your error log and tell you the steps.

### The database is empty?
Restore a backup. See [[How to restore a backup]].
"""),

    (C, "How to fix the WordPress white screen of death", "A blank white page usually means a PHP error. Find the cause with the error log, then switch off the plugin or theme that broke it.", False, """A blank white page, often called the white screen of death, is almost always a PHP error from a plugin, a theme or low memory.

## Find the cause

Open your hosting plan > **Logs** and read the newest lines of the error log. See [[How to read the error log of your website]]. The file path in the error names the plugin or theme.

## Fix it

1. Open **Website care > Plugins and themes**.
2. Switch off the plugin named in the log, or the last one you installed or updated.
3. Reload the website.
4. If the theme is the cause, switch to a default theme.

## If it is low memory

Raise the memory limit. See [[How to raise PHP memory limit, upload size and execution time]].

## Frequently asked questions

### Can I still use WP Admin?
If the admin is blank too, use the Plugins and themes manager in the panel.

### Will I lose my posts?
No. They are in the database.

### How do I prevent it?
Test updates on staging. See [[How to create a staging website to test changes safely]].
"""),

    (C, "How to fix a 500 Internal Server Error", "A 500 error means the server could not complete the request. Check the error log, .htaccess, plugins, PHP version and memory.", True, """A **500 Internal Server Error** is a general message: something failed on the server while handling the page.

## Check these in order

1. **Error log.** The newest lines usually name the cause. See [[How to read the error log of your website]].
2. **Plugins and themes.** Switch off the last one you changed.
3. **.htaccess file.** A wrong rule breaks the whole website. Rename it in the File manager and reload; WordPress can recreate it from Settings > Permalinks.
4. **PHP version.** An old plugin may not work on a new version, or the reverse. See [[How to change your PHP version]].
5. **Memory.** Raise the limit if the log mentions memory.
6. **File permissions.** Folders should be 755 and files 644.

## Frequently asked questions

### Is it my hosting's fault?
Usually it is a plugin, a rule or a script. The log tells.

### It happens only on one page?
The problem is in what that page loads.

### Still stuck?
Open a ticket with the domain and the log lines. See [[How to write a good support ticket]].
"""),

    (C, "How to fix 404 Not Found errors and broken permalinks", "Fix 404 errors on WordPress pages by resetting permalinks, checking the .htaccess file and the page address.", False, """A **404 Not Found** means the page does not exist at that address.

## If only posts and pages show 404

1. Sign in to WordPress.
2. Open **Settings > Permalinks** and press **Save changes** without changing anything. This rebuilds the rewrite rules.

## If the .htaccess file is missing

The File manager can show it in the website's folder. Create it with the standard WordPress rules, or save the permalinks again to recreate it.

## If a single page is missing

Check the page is published and its address is spelled correctly. If you changed the address, set a redirect from the old one.

## Frequently asked questions

### Why did it start after a migration?
The rewrite rules were not copied. Save the permalinks.

### Will redirects help?
Yes, for old addresses. See [[How to set up a domain redirect]].

### Does a 404 hurt SEO?
A few do not; fix important ones.
"""),

    (C, "How to fix a 403 Forbidden error", "A 403 means access is refused. Check file permissions, the .htaccess file, IP blocks and folder protection.", False, """A **403 Forbidden** means the server understood the request but refuses to allow it.

## Common causes and fixes

- **File permissions.** Folders should be 755 and files 644. Fix them in the File manager.
- **A missing index file.** A folder without `index.php` or `index.html` may be refused. Add the file.
- **.htaccess rules.** A rule may block access. Rename the file to test.
- **IP blocking.** Your own address may be blocked. See [[How to block an IP address from your website]].
- **Password-protected folder.** The area needs a login. See [[How to protect a folder with a password]].

## Frequently asked questions

### It happens only for me?
Your address may be blocked, or a security plugin may have blocked you.

### What about the admin area?
A security plugin can block it. Switch it off from the Plugins and themes manager.

### Is it a hack?
Sudden 403 errors with unknown files can be. Run a scan.
"""),

    (C, "How to fix a 503 Service Unavailable error", "A 503 means the server is too busy or the website is overloaded. Check resource use, plugins and traffic.", False, """A **503 Service Unavailable** means the server cannot handle the request right now.

## Why it happens

- Your website reached its CPU, memory or process limit.
- A plugin runs a heavy task.
- A traffic spike or bots.
- Maintenance on the server.

## What to do

1. Wait a minute and reload.
2. Open **Resource use** and see if you hit a limit. See [[How to check your resource usage (CPU, memory and processes)]].
3. Switch off heavy plugins and switch on **Faster pages**.
4. Check traffic and block bad addresses.
5. If it keeps happening, consider a bigger plan. See [[How to upgrade or change your hosting plan]].

## Frequently asked questions

### Is the website down for everyone?
Usually, but only while the limit is hit.

### Does it hurt SEO?
Short outages do not.

### Maintenance mode?
That is a separate "be right back" page you control.
"""),

    (C, "How to fix ERR_TOO_MANY_REDIRECTS (redirect loop)", "Fix 'This page isn't redirecting properly' by checking the HTTPS settings, the site address, plugins and .htaccess rules.", False, """The browser shows **ERR_TOO_MANY_REDIRECTS** when two rules keep sending it back and forth.

## Common causes

- **Force HTTPS** is on, and WordPress is also told to redirect to HTTP.
- The WordPress address and site address in Settings differ (http and https, or www and non-www).
- A redirect plugin or an old .htaccess rule.
- A proxy or CDN set to the wrong mode.

## Fix it

1. Clear your browser cookies for the site and try again.
2. Make sure the WordPress and Site addresses both use `https` and the same www choice. If you cannot sign in, change them from **Databases** (the `siteurl` and `home` options).
3. Rename .htaccess in the File manager to test; recreate it afterwards.
4. Switch off redirect or security plugins one by one.
5. Check **Force HTTPS** in the SSL page is not conflicting with a CDN.

## Frequently asked questions

### Why only after enabling HTTPS?
Two places are trying to do the same redirect.

### Can I edit the address without WordPress?
Yes, in the database options table. Back up first.

### Does it work in other browsers?
Test in a private window.
"""),

    (C, "How to fix mixed content warnings after switching to HTTPS", "Remove the 'not fully secure' padlock by changing http links, images and scripts to https on your WordPress website.", False, """After you enable HTTPS, the padlock may show a warning: **mixed content**. It means the page loads some things (images, scripts) over plain http.

## Find them

Open the page, press F12 and look at the Console. It lists each insecure address.

## Fix them

1. Make sure WordPress Settings > General uses `https` addresses.
2. Update old links in posts and pages. A search-and-replace plugin can change the old `http` address of your domain to the `https` one.
3. Update theme or plugin settings that hold absolute addresses.
4. Clear caches.

## Prevent it

Use `https` addresses when you add images and links. See [[How to get a free SSL certificate and switch on HTTPS]].

## Frequently asked questions

### Does it harm my website?
Browsers may block insecure parts, so the page can look broken.

### Does a CDN matter?
Make sure its address uses https too.

### Is it an SSL problem?
No. The certificate is fine; the page links are the issue.
"""),

    (C, "How to fix SSL and 'Not secure' warnings", "Fix 'Not secure', 'Your connection is not private' and certificate errors by requesting SSL, checking DNS and renewing the certificate.", True, """Browsers warn visitors when a website has no valid certificate.

## Common messages

- "Not secure".
- "Your connection is not private" (NET::ERR_CERT_COMMON_NAME_INVALID or similar).
- "Certificate expired".

## Fix it

1. Open your hosting plan > **SSL** and check the domain's status.
2. If there is no certificate or it expired, press **Request** or **Renew**. See [[How to get a free SSL certificate and switch on HTTPS]].
3. Make sure the domain leads to this hosting. See [[How to point your domain to our hosting]].
4. After it installs, switch on **Force HTTPS**.
5. Clear your browser cache and test in a private window.

## If the warning is for www only

The certificate must cover both names. Request it again including `www`.

## Frequently asked questions

### How long does it take?
A few minutes once DNS is right.

### Why did it expire?
Automatic renewal needs the domain to lead to this hosting.

### Does my email use this certificate?
Mail has its own names; use the server name on the mailbox page.
"""),

    (C, "How to fix a slow website: a step-by-step checklist", "Find out why your website is slow and fix it: measure, check resources, plugins, images, PHP, caching and the plan.", False, """A slow website loses visitors. Follow this checklist.

## 1. Measure

Open **Website care > Speed** and run the test. Note the server response and page size.

## 2. If the server responds slowly

Open **Resource use**. If CPU or memory is at the limit, reduce load or upgrade. See [[How to check your resource usage (CPU, memory and processes)]].

## 3. If the page is heavy

Compress images, remove unused plugins and avoid sliders and huge fonts.

## 4. Turn on caching

Switch on **Faster pages** and use a caching plugin if needed.

## 5. Update

Use a current PHP version and update WordPress, themes and plugins.

## 6. Check for malware

A hacked website can be very slow. Run a scan.

## Related guide

[[How to speed up your WordPress website]].

## Frequently asked questions

### Is my hosting too slow?
Check server response and resource use before blaming the plan.

### Do ads and trackers slow it?
Yes, every external script adds time.

### Is there a quick win?
Optimising images is usually the biggest one.
"""),

    (C, "How to fix 'DNS_PROBE_FINISHED_NXDOMAIN' and a domain that does not open", "Fix a domain that does not open or shows DNS_PROBE_FINISHED_NXDOMAIN by checking nameservers, DNS records and expiry.", False, """**DNS_PROBE_FINISHED_NXDOMAIN** means the browser could not find the domain in DNS.

## Check in this order

1. **Is the domain registered and not expired?** Open **Domains** or use the WHOIS lookup. See [[How to renew your domain name]].
2. **Nameservers.** They must point to your DNS. See [[How to change nameservers]].
3. **A record.** There must be an A record for `@` and `www`. See [[How to add an A record to point a domain or subdomain]].
4. **Wait.** New or changed records take time. See [[Why DNS changes take time to spread]].
5. **Your device.** Try another network or clear your computer's DNS cache.

## Frequently asked questions

### Did I lose my domain?
Check the expiry date first.

### It works on my phone but not my computer?
Your computer cached the old answer. Restart it or flush DNS.

### Is it my hosting?
If the domain does not resolve, hosting is not even reached yet.
"""),

    (C, "How to get back into WordPress if you are locked out", "Regain access to WordPress when you forgot the password, the reset email never comes or a plugin blocks the login.", False, """Locked out of WordPress? There are several ways back in.

## Sign in without a password

Open the panel, find your website in **Websites** and press **WP Admin**. See [[How to sign in to WordPress without a password]].

## Set a new password

Press **Set a new password** on the website's page. See [[How to set a new WordPress password]].

## If a plugin blocks the login

Open **Website care > Plugins and themes** and switch off security or login plugins. Reload the login page.

## If you changed the site address by mistake

Fix `siteurl` and `home` in the database. See [[How to browse and edit database tables in the panel]].

## Frequently asked questions

### The reset email does not arrive?
Use the panel's new password option instead.

### I forgot the user name?
It is shown on the website's page.

### Is someone else in my site?
Change all passwords and run a scan.
"""),

    (C, "How to fix email bounce messages such as 550 and 5.7.1", "Understand common bounce codes and fix them: unknown address, full mailbox, blocked by spam checks and failed SPF or DKIM.", False, """A **bounce message** comes back when an email cannot be delivered. It always contains the reason.

## Common codes

- **550 user unknown**: the address does not exist. Check the spelling.
- **552 or "mailbox full"**: the recipient's mailbox is full.
- **550 5.7.1 rejected as spam / blocked**: the receiver distrusts your message. Add SPF, DKIM and DMARC. See [[How to stop your email going to spam]].
- **554 relay denied**: your mail program is not signed in to the outgoing server.
- **Timeout**: the receiving server was unreachable; try later.

## What to do

1. Read the line that explains the reason.
2. Fix the cause.
3. Send again.

## Frequently asked questions

### Is the problem on my side?
The bounce text tells. If it names SPF or DKIM it is your domain's records.

### Why only some recipients?
Their provider may be stricter.

### Where do I get help?
Open a ticket and paste the whole bounce. See [[How to write a good support ticket]].
"""),

    (C, "How to fix WordPress upload errors and 'file exceeds the maximum upload size'", "Upload large images, themes or backups to WordPress by raising PHP upload and memory limits in the panel.", False, """WordPress says "The uploaded file exceeds the upload_max_filesize directive" when a file is bigger than the PHP limit.

## Fix it

1. Open your hosting plan > **PHP** and choose your domain.
2. Raise **Upload size** a little above your file's size, and the **Memory limit** if needed.
3. Save and try again.

See [[How to raise PHP memory limit, upload size and execution time]].

## Other upload problems

- **HTTP error** on images: raise the memory limit or reduce the image size.
- **Cannot write to folder**: fix permissions (folders 755).
- **Not enough disk space**: free space. See [[Hosting plan basics: disk space, bandwidth and resources explained]].

## Frequently asked questions

### What is a sensible limit?
64M to 256M for most websites.

### Can I upload by FTP instead?
Yes, see [[How to create an FTP account and upload files]].

### The setting did not change?
Check you picked the right domain and clear caches.
"""),

    (C, "How to fix 'Briefly unavailable for scheduled maintenance'", "Remove the stuck 'Briefly unavailable for scheduled maintenance' message that appears after an update was interrupted.", False, """WordPress shows **"Briefly unavailable for scheduled maintenance. Check back in a minute."** while it updates. If it stays after the update, it is stuck.

## Fix it

1. Open the **File manager** and go to your website's folder.
2. Find the hidden file `.maintenance` and delete it.
3. Reload your website.

## Finish the update

Open **Website care > WordPress updates** or the WordPress dashboard and run the update again. If a plugin failed, switch it off.

## Prevent it

Do not close the page during an update and test big updates on staging. See [[How to create a staging website to test changes safely]].

## Frequently asked questions

### Can I not see the .maintenance file?
Switch on "show hidden files" in the File manager.

### Is my website broken?
No. Only the notice is stuck.

### Will it come back?
Only if an update is interrupted again.
"""),
]
