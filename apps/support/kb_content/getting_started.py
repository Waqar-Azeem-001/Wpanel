"""Getting started: the customer panel, the first website, the domain and the words used in hosting."""
C = "getting-started"

ARTICLES = [
    (C, "A tour of your customer panel", "Learn where everything is in the customer panel: websites, domains, email, billing and support, and how to jump to any page with Ctrl K.", True, """The customer panel is where you manage everything you buy from us. This tour shows the main places, so you can find any tool in a few seconds.

## The menu and the search box

The menu on the left lists the main areas. At the top there is a search box: press **Ctrl K** (or click it), type part of a page name such as "backups" or "invoices" and press Enter to jump straight there. On a phone the menu opens with the button at the top left.

## The main areas

- **Home**: your account at a glance, with anything that needs attention such as an unpaid invoice or an expiring service.
- **Websites**: every website on your hosting plans. Create a website, open WordPress, run Website care, make a staging copy or delete a site.
- **More services**: other products you can order.
- **Domains**: search, register, transfer, renew and manage your domain names and their DNS.
- **Emails**: all the mailboxes of your domains in one list.
- **Orders** and **Billing**: what you bought, your invoices, payments and renewals.
- **Support**: tickets, this help centre and the AI Agent.
- **Refer and earn**: your personal link and the commission you earn.

## Your hosting plan page

Open **Websites** and choose a hosting plan to reach its tools: Files, Databases, Email, Domains, DNS, SSL, PHP, Cron jobs, FTP, Backups, Logs, Traffic, Resource use and SSH. If your plan or server does not offer a tool, its page says so instead of showing an error.

## Help is always close

The **AI Agent** button at the top of every page opens a chat that can answer how-to questions and look at your own account. See "How to use the AI Agent" for details.

## Frequently asked questions

### How do I find a page quickly?
Press Ctrl K, type a few letters of the page name and choose it from the list.

### Where is my hosting plan?
Open Websites. Each hosting plan is listed there with its websites, and its tools are in the plan's menu.

### Can I use the panel on my phone?
Yes. The panel adapts to small screens and the menu opens from the button at the top left.
"""),

    (C, "How to create your first website", "Create a WordPress or custom website on your hosting plan in a few minutes, with every installation step shown and a progress percentage.", True, """Your hosting plan is ready as soon as it is active. The next step is to create the website that will live on it. You can choose **WordPress** (the most popular website system) or a **custom website** where you upload your own files.

## Create a WordPress website

1. Open **Websites** and choose your hosting plan.
2. Press **Create website** and choose **WordPress**.
3. Write the website name and choose an administrator user name, email address and a strong password. Save the password: it is not shown again.
4. Press the button to start the installation.
5. Watch the progress page. It shows each step with a percentage: the database, the download, the files, the settings and the final touches.
6. When you see **Your website is ready**, press **WP Admin** to sign in without typing a password.

## Create a custom website

Choose **Custom website** instead of WordPress. We create the website folder and a starter page. Then upload your own files with the File manager or with FTP.

## What if the folder already has files?

We never install over existing files. If the folder holds files, move or delete them in the File manager first, or choose a custom website.

## Frequently asked questions

### How long does the WordPress installation take?
Usually a minute or two. The progress page refreshes by itself and you can leave it: the website will be ready when you return.

### Can I change the website name later?
Yes. Open WordPress, go to Settings, General and change the Site Title.

### Do I need to buy a domain first?
No. You can create the website and look at it on a preview address, then point your domain to it when you are ready.
"""),

    (C, "How to point your domain to our hosting", "Point a domain name to your hosting by changing its nameservers. Learn which nameservers to use, where to set them and how long it takes.", False, """A domain name opens your website only when its **nameservers** lead to the hosting where the website lives. If you registered your domain somewhere else, you need to change its nameservers once.

## Find the nameservers to use

Open **Domains**, choose your domain and look at the nameservers shown for your hosting plan. They look like `ns1.example.com` and `ns2.example.com`. Copy both.

## Set them at your registrar

1. Sign in to the company where the domain is registered.
2. Find the domain's DNS or nameserver settings.
3. Choose custom nameservers and replace the old ones with ours.
4. Save the change.

## Wait for DNS to update

The change can take from a few minutes up to a few hours to be seen everywhere. This waiting time is called DNS propagation. During this time some visitors may still see the old website.

## Check that it worked

Open **Websites** and choose your website. The page tells you whether the domain already leads to your hosting. You can also type your domain in a browser from a different network.

## Frequently asked questions

### Do I have to change nameservers if I only want email elsewhere?
No. If you keep your DNS here, you can add MX records that send your email to another provider. See "How to add MX records for email".

### What if my domain is registered with you?
Then the nameservers can be set for you from the domain's page, without visiting another company.

### Will my website go offline while I change nameservers?
The old website stays reachable until the change spreads. Copy your website to the new hosting before you switch to avoid a gap.
"""),

    (C, "How to open your website before the domain points here", "Use the preview address to see and edit a new WordPress website while your domain's DNS is still changing.", False, """After you create a WordPress website, your domain may not lead to your hosting yet. A **preview address** lets you open and edit the website anyway, so you can start working right away.

## How to open the preview

1. Open **Websites** and find your website.
2. Press **Open website** or **WP Admin**.
3. If the domain does not lead to our hosting yet, we open the preview address for you instead of an empty page.

## What the preview shows

The preview shows exactly the same website. Nothing is copied, and every change you make is saved in the real website. When your domain's nameservers are changed, your own address opens the same site.

## When to use it

- You are building a new website before launching it.
- You moved your domain to us and DNS is still updating.
- You want to check the website without telling visitors yet.

## Frequently asked questions

### Can search engines find the preview address?
It is meant for you. For a website still under construction, also switch on the option that asks search engines not to index it in Website care.

### Is the preview secure?
It uses the same login protection as the rest of the panel and a private token. Do not share the link with people you do not trust.

### Why does my own domain still show another website?
Your domain's nameservers still point somewhere else. Follow "How to point your domain to our hosting".
"""),

    (C, "Hosting plan basics: disk space, bandwidth and resources explained", "What disk space, bandwidth, inodes, CPU and memory mean on a hosting plan, and how to see how much of each your website uses.", False, """Every hosting plan has limits. Knowing what they mean helps you pick the right plan and avoid slowdowns.

## Disk space

Disk space (storage) is the room for your website files, databases, backups and email. Open your hosting plan and check **Resource use** or the usage figures to see how much is used.

## Bandwidth

Bandwidth is the amount of data sent to your visitors each month. A busy website with large images or videos uses more. The **Traffic** page shows visits and pages viewed.

## CPU, memory and processes

These show how hard your website works the server right now. If you often reach the limit, pages become slow or show errors. Check **Resource use** for a history of the last 24 hours.

## What to do when you reach a limit

- Delete old backups and files you do not need.
- Compress large images and switch on **Faster pages** in Website care.
- Remove plugins you do not use.
- Move to a bigger plan if the website has outgrown the plan.

## Frequently asked questions

### What happens if I exceed my disk space?
Uploads and email may fail until you free space or upgrade.

### Is bandwidth the same as speed?
No. Bandwidth is an amount of data per month. Speed is how fast pages load.

### Where can I see my plan's limits?
On the hosting plan's page and in Resource use.
"""),

    (C, "What is cPanel and how do you use its tools here?", "cPanel is the control software behind most shared hosting. Learn how its tools appear in your customer panel and where to find them.", False, """**cPanel** is the control software used by many hosting servers. It manages files, databases, email and domains. You usually do not need to open cPanel itself: the tools you use most are built into your customer panel.

## What is built in

- **Files** and **FTP** for your website's files.
- **Databases** to create and edit databases.
- **Email** for mailboxes, forwarders and autoresponders.
- **Domains** and **DNS** to manage names and records.
- **SSL**, **PHP**, **Cron jobs**, **Backups**, **Logs** and **Traffic**.

## Why built in?

You stay in one place with one sign-in. Each tool shows only what your plan allows, with simple steps and clear messages.

## Your hosting password

Your hosting (cPanel) password is separate from your account password. You can set a new one yourself. See "How to change your hosting password".

## Frequently asked questions

### Do I need a cPanel login?
Most things can be done in the panel. If you need cPanel itself for something special, ask us.

### Is my cPanel password the same as my account password?
No. They are separate, so a leak of one does not open the other.

### Which tool do I use to change PHP?
Open your hosting plan and choose PHP.
"""),

    (C, "WordPress or a custom website: which should you choose?", "Compare WordPress and a custom HTML or PHP website to choose the right way to build your site, with simple guidance for beginners.", False, """When you create a website you choose between **WordPress** and a **custom website**. The right choice depends on what you want to build.

## Choose WordPress when

- You want a blog, business website, portfolio or online shop.
- You want to edit content without coding.
- You want thousands of ready themes and plugins.

## Choose a custom website when

- You already have your own HTML, PHP or other files.
- A developer builds the website for you.
- You want a very small, simple page with no database.

## Can I change later?

You can install WordPress later in another folder or on another domain of your plan. Moving between the two means copying content, so decide before you invest time.

## Which is faster?

A small hand-written page is very fast. A well-cared WordPress website with caching is also fast. Use Website care to test your speed.

## Frequently asked questions

### Is WordPress free?
Yes, WordPress itself is free. Some themes and plugins are paid.

### Do I need to know code?
Not for WordPress. For a custom website you or your developer write the code.

### Can I have both?
Yes, if your plan allows more than one website. Add another website on a second domain.
"""),

    (C, "How to find your hosting details", "Find your hosting plan's domain, username, server and renewal date in the panel when you need them for a developer or support.", False, """Developers and support often ask for details about your hosting. You can find them yourself in a few clicks.

## Where to look

1. Open **Websites** and choose your hosting plan.
2. Open the plan's **Information** page.
3. You will see the main domain, the plan name, the status, the username and the renewal date.

## Details people usually ask for

- **Domain**: the main domain of the plan.
- **Username**: used with FTP and SSH.
- **Server**: the server the plan lives on.
- **Renewal date**: when the plan must be renewed.

## Keep your password private

Never send your hosting password to anyone. If a developer needs access, create a separate FTP account for them in the plan and delete it afterwards.

## Frequently asked questions

### Can I give a developer access without my password?
Yes. Create an FTP account just for them and remove it when the work is done.

### Where is the server's IP address?
Your DNS page shows the address your website points to.

### How do I check my renewal date?
It is on the Information page and in Billing.
"""),

    (C, "Launch checklist: what to do after your website is ready", "A simple checklist before you announce your website: SSL, backups, speed, email, search engine settings and security.", False, """Your website is ready. Before you tell the world, go through this short checklist.

## 1. Domain and SSL

Make sure your domain leads to your hosting and that SSL is active so visitors see the padlock. Then switch on **Force HTTPS**.

## 2. Backups

Create a backup now and choose an automatic schedule in Website care.

## 3. Updates and security

Update WordPress, plugins and themes. Remove anything you do not use. Run a malware scan.

## 4. Speed

Run a speed test and switch on **Faster pages**.

## 5. Search engines

If you used the option that asks search engines not to index your website while building, switch it off before launch so people can find you.

## 6. Email

Create your mailboxes and test sending and receiving. Add SPF and DKIM records for good delivery.

## 7. Contact and policies

Check your contact page, forms and legal pages.

## Frequently asked questions

### How long before search engines find my website?
Usually days to weeks. Submitting a sitemap can help.

### Should I test on my phone?
Yes. Most visitors use phones.

### Do I need a backup if my host makes backups?
Yes. Keep your own copy of important data.
"""),

    (C, "How to use the search and shortcuts in the panel", "Jump to any page of the customer panel with Ctrl K and use the menu, the Help button and the AI Agent to save time.", False, """The panel is designed so you can reach any page with very few clicks.

## Quick search (Ctrl K)

Press **Ctrl K** on your keyboard, or click the search box at the top. Type part of a page name, for example "dns", "invoice" or "backup". Use the arrow keys and Enter to open a result.

## The menu

The menu on the left groups related pages. Websites lead to a hosting plan's tools. Domains lead to DNS and renewals.

## Breadcrumbs

Above each page title you see where you are, such as Home / Websites / yourdomain.com. Click any part to go back.

## Help and the AI Agent

The **Help** button opens this help centre. The **AI Agent** button opens a chat to ask questions in your own words.

## Frequently asked questions

### Does Ctrl K work on a Mac?
Press Command K, or click the search box.

### Can I search for a domain name?
Yes. Type part of the domain to jump to its page.

### Why do I not see some pages?
The panel shows only what your account can use.
"""),

    (C, "How to see your plan's renewal date and what is included", "Check when your hosting plan renews, what it includes and how to renew or upgrade it from the customer panel.", False, """It is a good habit to know when each service ends so nothing expires by surprise.

## Check the renewal date

Open **Websites**, choose your plan and open **Information**. The renewal date is listed. The same date appears in **Billing** next to the service.

## What your plan includes

The plan page shows the plan name, the number of websites you can host, disk space and other limits. Open **Resource use** to see how much you use.

## Renew or upgrade

A renewal invoice is created before the plan ends. Pay it any time before the due date. To move to a bigger plan, open **Plans**, choose one and order it. Tell us if you want your website moved and we will help.

## Frequently asked questions

### Do I get a reminder?
Yes. We send emails before a service ends and show a notice on Home.

### What happens if I forget to renew?
The service can be suspended after the due date and later removed. Pay the invoice to bring it back.

### Can I renew for several years?
Domains can usually be renewed for more than one year. Hosting renewal periods are shown when you renew.
"""),

    (C, "Hosting and domain terms explained: a simple glossary", "A plain-language glossary of hosting, domain and email terms such as DNS, nameserver, SSL, FTP, IMAP, PHP and WordPress.", False, """New to hosting? These short explanations cover the words you will meet most often.

## Domain and DNS words

- **Domain name**: your address on the internet, such as `example.com`.
- **DNS**: the internet's phone book; it turns a domain into the address of a server.
- **Nameserver**: the server that holds your DNS records.
- **DNS record**: one line in DNS (A, CNAME, MX, TXT and others).
- **Propagation**: the time a DNS change takes to reach everyone.

## Hosting words

- **Hosting plan**: the space and resources for your website.
- **Control panel**: the website where you manage hosting (this panel).
- **FTP / SFTP**: ways to upload files.
- **SSH**: a secure command-line connection for advanced users.
- **PHP**: the programming language that runs WordPress.
- **Database**: where WordPress stores posts and settings.
- **Cron job**: a task that runs on a schedule.

## Security words

- **SSL certificate**: proves your website is yours and encrypts traffic (HTTPS).
- **Backup**: a saved copy of your website.
- **Malware**: harmful code placed on a website.

## Email words

- **IMAP / POP3**: ways to read email.
- **SMTP**: the way email is sent.
- **SPF, DKIM, DMARC**: records that prove your email is genuine.

## Frequently asked questions

### What is the difference between a domain and hosting?
A domain is your name. Hosting is the computer space where your website lives. You need both.

### What is a subdomain?
A prefix on your domain, such as `shop.example.com`.

### What does uptime mean?
The percentage of time your website is reachable.
"""),
]
