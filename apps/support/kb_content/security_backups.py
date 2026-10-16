"""Security and backups: SSL, HTTPS, backups and restores, malware, passwords and what to do after a hack."""
C = "security-backups"

ARTICLES = [
    (C, "How to get a free SSL certificate and switch on HTTPS", "Request a free SSL certificate for your domain, wait for it to install and turn on the HTTPS redirect so visitors see the secure padlock.", True, """An **SSL certificate** makes visitors reach your website over a secure connection (HTTPS, the padlock). It also helps search rankings and trust.

## Request a certificate

1. Open **Websites**, choose your hosting plan and open **SSL**.
2. Find your domain and press **Request** (or **Renew**).
3. The certificate is free and is installed in a few minutes. It renews by itself afterwards.

## Send everyone to HTTPS

When the certificate works, switch on **Force HTTPS**. The panel refuses to switch it on without a working certificate, because visitors would see a security warning.

## Requirements

Your domain must lead to this hosting. If the request fails, check the nameservers. See [[How to point your domain to our hosting]].

## Frequently asked questions

### Is the certificate really free?
Yes, for your domains on this hosting.

### How often does it renew?
Automatically, before it expires. The SSL page shows the days left.

### The browser still warns me?
See [[How to fix SSL and 'Not secure' warnings]].
"""),

    (C, "How to create and download a backup", "Create a backup of your website files, database or emails and download it to your computer for safe keeping.", True, """A backup is a saved copy of your website that you can bring back after a mistake or a hack.

## Create a backup

1. Open your hosting plan > **Backups**.
2. Press **Create backup**.
3. Choose the kind: files, database, email or full.
4. When it appears in the list it is ready.

## Download it

Press **Download** next to a backup and keep the file on your computer or in cloud storage. A backup stored only on the same server is not enough protection.

## When to create one

Before every update, before editing the database and before big changes.

## Frequently asked questions

### How big is a backup?
About as big as the data it holds.

### Can I choose what to include?
Yes, by choosing the kind.

### How long are backups kept?
Backups you create stay until you delete them. Automatic ones are rotated.
"""),

    (C, "How to restore a backup", "Bring your website back from a backup safely: what a restore replaces, how to prepare and what to check afterwards.", False, """A restore puts the files or data from a backup back on your hosting.

## Before you restore

A restore **replaces** the current files or data it covers. Create a fresh backup first. See [[How to create and download a backup]].

## Restore

1. Open your hosting plan > **Backups**.
2. Choose the backup and press **Restore**.
3. Confirm. It can take a few minutes.

## After the restore

Open your website and check pages, forms and the admin area. If you restored a database backup, clear any cache.

## Let the AI Agent help

The AI Agent can prepare a restore. Because it replaces data, it needs a security code from your email.

## Frequently asked questions

### Can I restore only one file?
Download the backup, extract it and upload the file with the File manager.

### Will emails come back?
Only if the backup covers email.

### What if the restore fails?
The page shows the reason; your fresh backup lets you return to where you were.
"""),

    (C, "How to set up automatic backups", "Choose a daily or weekly schedule for automatic website backups so you always have a recent copy, without remembering.", False, """Automatic backups protect you even when you forget.

## Choose a schedule

1. Open **Websites**, choose your website and open **Website care**.
2. In **Automatic backups** choose a schedule such as daily or weekly.
3. Press **Save**.

## How many are kept?

The newest three automatic backups are kept. Older ones are removed so your storage does not fill up.

## Keep a copy elsewhere

Download important backups to your computer. See [[How to create and download a backup]].

## Frequently asked questions

### Do automatic backups cost extra?
They use your plan's disk space.

### Can I turn them off?
Yes. Choose Off.

### Is it enough?
Combine automatic backups with your own downloads before big changes.
"""),

    (C, "How to scan your website for malware and use quarantine", "Run a malware scan, understand the result, use quarantine for suspicious files and restore a file moved by mistake.", True, """Malware is harmful code hidden in your website. A scan looks for it.

## Run a scan

1. Open **Websites**, choose your website and open **Website care**.
2. In **Malware scan** press **Scan now**, or choose **Scan automatically** (daily or weekly).
3. The result lists what was found and why.

## Quarantine

Suspicious files are moved to **quarantine**. They are not deleted, so a good file moved by mistake can be restored from the same page.

## If something is found

Follow [[What to do if your website is hacked]].

## Frequently asked questions

### Will a scan find everything?
No scanner is perfect. Keep software updated too.

### Does the scan change my files?
It only looks, then moves files to quarantine when asked.

### How often should I scan?
Weekly is a good start, daily for busy websites.
"""),

    (C, "How to protect a folder with a password", "Make a private area on your website, such as a staging copy or a client preview, by asking for a user name and password.", False, """You can put a password on any folder of your website.

## Steps

1. Open your hosting plan > **Advanced** and choose **Protect a folder**.
2. Choose the folder.
3. Add a user name and password.
4. Save.

Visitors of that folder and everything inside it see a sign-in box first.

## Remove the protection

Open the same page and remove it when you no longer need it.

## Good to know

- Use a strong password.
- Do not protect the folder that holds your public website.
- Test in a private browser window.

## Frequently asked questions

### Does it hide the folder from Google?
Yes, search engines cannot get past the password.

### Does it slow my website?
No.

### Can I protect single files?
Protect the folder that holds them.
"""),

    (C, "How to check that your files have not been changed (integrity check)", "Take a baseline of your files and check later whether any file was added, changed or removed, to spot a hack early.", False, """An **integrity check** compares your files with a trusted snapshot to show what changed.

## How it works

1. Create a **baseline** when your website is in a good state.
2. Run **Check** later. It lists files that were added, changed or removed.

## When it helps

After an update you did not expect, or when your website behaves oddly. Unexpected changes in core files are a warning.

## After an update

Take a new baseline after you update on purpose, so the next check compares with the new state.

## Frequently asked questions

### Is it a replacement for a malware scan?
No. Use both. See [[How to scan your website for malware and use quarantine]].

### Do plugin updates show up as changes?
Yes. That is why you create a new baseline afterwards.

### Does it run by itself?
Run it when you want a check.
"""),

    (C, "How to keep WordPress secure: a practical checklist", "Follow a simple checklist to protect your WordPress website: updates, strong passwords, backups, scans, fewer plugins and safe habits.", True, """Most hacks use the same few weaknesses. Close them and your website is much safer.

## Checklist

1. **Update everything.** WordPress, themes and plugins. See [[How to turn on automatic WordPress updates]].
2. **Use strong, unique passwords** for WordPress, hosting and email.
3. **Remove what you do not use**: old plugins, themes and users.
4. **Backups.** Automatic schedule and a downloaded copy. See [[How to set up automatic backups]].
5. **Scan** regularly. See [[How to scan your website for malware and use quarantine]].
6. **HTTPS** everywhere. See [[How to get a free SSL certificate and switch on HTTPS]].
7. **Limit administrators.** Give people only the role they need.
8. **Download plugins only from trusted sources.**

## Frequently asked questions

### Do I need a security plugin?
A good one can help, but updates and backups matter more.

### Is changing the login URL useful?
It reduces noise, but it is not a real defence.

### What about two-step login?
It is a good extra layer if you use a plugin for it.
"""),

    (C, "What to do if your website is hacked", "Steps to take when your WordPress website is hacked: stay calm, take a backup, scan, change passwords, clean, update and ask for help.", False, """A hacked website is stressful but fixable. Work through these steps in order.

## 1. Stay calm and make a backup

Create a backup of the current state so nothing is lost while you work.

## 2. Scan

Run a malware scan and read the result. See [[How to scan your website for malware and use quarantine]].

## 3. Change passwords

Change WordPress, hosting, FTP and email passwords. Use new, strong ones.

## 4. Clean and update

- Remove unknown files and users.
- Reinstall WordPress core, plugins and themes from trusted sources.
- Update everything.

## 5. Restore if needed

Restore a clean backup from before the hack. See [[How to restore a backup]].

## 6. Ask for help

Open a ticket with the domain, what you saw and when. See [[How to write a good support ticket]].

## Frequently asked questions

### How did they get in?
Usually an outdated plugin, a weak password or a pirated theme.

### Will Google warn visitors?
It may. Once clean, request a review in Google Search Console.

### Can it happen again?
Yes, if the cause is not fixed. Follow the security checklist.
"""),

    (C, "How to create strong passwords and keep them safe", "Create long, unique passwords for your hosting, WordPress and email, and keep them in a password manager.", False, """Weak and reused passwords are the easiest way into your accounts.

## What makes a password strong

- At least 12 characters; longer is better.
- A mix of words, numbers and symbols, or a long phrase.
- Different for every account.

## Keep them safe

Use a **password manager** to create and store passwords. Never write them in an email, a chat or a shared document.

## Where passwords are used here

Your customer account, your hosting (cPanel, FTP, SSH), WordPress and each mailbox. See [[How to change your hosting password]].

## Frequently asked questions

### Is a long phrase better than symbols?
A long random phrase is easier to remember and very strong.

### How often should I change passwords?
When you suspect a leak, or when someone who knew it leaves.

### Does our team ask for passwords?
Never.
"""),
]
