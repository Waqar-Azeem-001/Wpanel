"""Longer sections for the email and hosting tool articles."""

EXTRAS = {
    # ---- email --------------------------------------------------------------------------------------------------------------------------------
    "How to create a mailbox and open webmail": """## Naming mailboxes

Use short, role-based names for shared addresses (`info`, `sales`, `support`) and personal names for people. Avoid numbers and underscores; they are harder to say on the phone.

## Passwords and recovery

Give every mailbox its own strong password. If someone leaves, change the password at once or delete the mailbox after you forward its mail. Keep a record of who uses which mailbox.""",

    "How to set up email on your phone or Outlook": """## Which security option

Choose SSL/TLS and the ports shown on the mailbox page. Avoid "no security". If your program offers to detect settings automatically, check them against the page.

## Keep mail in sync

With IMAP, deleting a message on your phone deletes it everywhere. Create folders in webmail and they appear in every program. For a shared mailbox, make sure each person understands that reading, moving and deleting are shared actions.""",

    "IMAP or POP3: which should you choose?": """## Switching safely

If you used POP3 on one computer and want IMAP, first copy your local mail into an IMAP folder so it also lives on the server. Then remove the POP3 account.

## Backups

IMAP keeps your mail on the server, which is backed up with your account where backups include email. POP3 moves mail to your computer, so the computer needs its own backup.""",

    "How to forward email to another address": """## Typical uses

Send `info@` to the owner's personal inbox, send `sales@` to several people, or keep an old address working after a rename. Keep forwarders until nobody writes to the old address any more.

## Reply from the right address

A forwarder only receives. To reply as `info@`, add that address to your mail program or webmail as a sender, using its mailbox login.""",

    "How to set up an automatic reply (autoresponder)": """## Good messages

Say who you are, when you will reply and who to contact for urgent matters. Do not write personal details such as the dates you are away from home. Keep a friendly, short tone.

## Avoid loops

Do not set an autoresponder on an address that forwards to another autoresponder. If two reply to each other, a loop starts. Delete the autoresponder as soon as you are back.""",

    "What to do when a mailbox is full": """## Prevent it

Set a sensible size at the start, empty Spam and Trash monthly and keep large attachments in cloud storage instead of in mail. Watch the size from the Emails page.

## What the sender sees

When your mailbox is full, senders get a message that it cannot receive mail. Important mail can be lost if they do not try again, so fix the problem quickly.""",

    "How to adjust your spam filter": """## Finding the balance

A filter that is too relaxed fills the inbox with junk. One that is too strict hides real mail. Start with the default and change only if you see a pattern: missed good mail or too much junk.

## Train your mail program

In webmail, mark junk as spam and good mail as "not spam" so the system learns. Check the Spam folder every few days at first, until you are sure good messages are arriving.""",

    "How mail routing works: local or remote delivery": """## Check after changes

After you change routing, send a test message from another account and look at where it lands. If a different provider handles your mail, local delivery on this server would capture mail meant for the provider.

## A simple rule

Mailboxes here: local. Mail handled elsewhere: remote. If you are not sure, look at your MX records: they say where mail goes.""",

    "How to stop your email going to spam": """## Warm up a new domain

A new domain has no reputation. Start with small volumes of wanted mail, ask friends to reply and avoid big mailings in the first weeks.

## Check your authentication

Send a message to a mailbox where you can see the full headers. SPF, DKIM and DMARC should show "pass". If one fails, fix its record. Also check that your website's contact form sends from an address on your domain.""",

    "Why you cannot send or receive email: a checklist": """## Narrow it down

Test with webmail first. If webmail works, the server is fine and the problem is in the mail program. If webmail fails too, check the quota, routing and DNS. If only one person has trouble, compare their settings with another person's.

## Keep evidence

When you ask for help, send the exact error text, the time, the sender and the recipient. A bounce message contains a technical reason that points straight to the cause.""",

    "How to delete a mailbox safely": """## Keep access to old mail

Before deleting, export or forward the messages you must keep. For business records, keep an archive. Tell contacts the new address and set a forwarder for a while.

## What stays after deletion

The domain and its other mailboxes stay as they are. The deleted address can be created again later, but its old mail is gone.""",

    "Email limits and good habits for sending": """## Newsletters

Use a service built for newsletters. They handle unsubscribe links, bounces and complaints, which protects your domain's reputation. Send from an address on your own domain and authenticate it.

## Watch for signs

A sudden increase in bounce messages or a queue of unsent mail often means a hacked website or mailbox. Change passwords, scan your website and tell us.""",

    # ---- hosting tools ------------------------------------------------------------------------------------------------------------------------
    "How to use the File manager": """## Work safely

Download a copy before you edit an important file such as wp-config.php or .htaccess. Make small changes and test after each one. Do not delete folders you do not recognise: WordPress, cache and email use several.

## Faster uploads

Upload a single zip of many files and extract it in the File manager. It is quicker and keeps the folder structure.""",

    "How to create an FTP account and upload files": """## Limit the access

Restrict each FTP account to the folder it needs. A developer for one website does not need access to all your files. Delete accounts when the work ends.

## Avoid common upload problems

Use binary mode for images, do not upload over a slow Wi-Fi without a retry, and keep file names simple without spaces or special characters.""",

    "How to create a database and a database user": """## Naming and passwords

Use a clear database name that tells you which website it belongs to. Give each website its own database and user; sharing one user across sites makes a leak worse.

## Backups

Include your databases in your backups. A website's files without its database cannot be restored to a working site.""",

    "How to browse and edit database tables in the panel": """## Typical safe edits

Fix a wrong site address in the options table, deactivate a plugin by editing its entry, or change a user's email. Read the row carefully and copy the old value somewhere before you change it.

## What not to touch

Leave serialised values, order tables and anything you do not understand. If you break a website this way, restore the backup you took.""",

    "How to change your PHP version": """## When to change

Change when a plugin says it needs a newer version, when your current version is end of life or when you want speed. Do it at a quiet time and test the main pages afterwards.

## Compatibility tips

Update your themes and plugins first. If an old plugin refuses to work on a newer PHP, replace it with a maintained one rather than keeping an old PHP.""",

    "How to raise PHP memory limit, upload size and execution time": """## Why limits exist

They stop a faulty script from using the whole server. Raising a limit a little solves normal needs; raising it to the maximum hides real problems such as a heavy plugin.

## Test after changing

Repeat the action that failed: the upload, the import or the page that errored. If it still fails, read the error log for the next clue.""",

    "How to switch PHP extensions on or off": """## Common extensions

`imagick` and `gd` for images, `intl` for languages, `mbstring` for text, `zip` for archives, `curl` for connections to other services. WordPress works with most defaults, but some plugins need extras.

## Keep it tidy

Switch on only what your website needs. If a plugin says an extension is missing, switch it on and test again.""",

    "How to set up a cron job": """## Choose a sensible schedule

Run heavy tasks at night and light tasks more often. Do not run jobs every minute unless you really need to; many plans do not allow it.

## Test and watch

After creating a job, run it once by hand, check its result and look at the error log the next day. A job that fails silently is worse than no job.""",

    "How to read the error log of your website": """## Reading strategy

Clear the log, repeat the problem once, then read only the new lines. Start from the first error after your action: later lines are often only consequences.

## Share it with a developer

Copy the few relevant lines, not the whole file, and add the time. Remove anything private such as passwords before you share.""",

    "How to see your website traffic and visitors": """## Using the numbers

Look at the trend, not single days. Compare weeks. A sudden jump may be a good article, a bot or an attack. Check the top pages to see what visitors value.

## Combine with other tools

Server statistics show requests; a tool such as Google Analytics shows behaviour. Use the panel's numbers to check for unusual load and the other tool for marketing decisions.""",

    "How to check your resource usage (CPU, memory and processes)": """## Reading the chart

A flat low line is healthy. Regular peaks at the same time may be a backup, a cron job or a visitor pattern. A line stuck at the limit means requests wait and your website slows.

## Typical culprits

Heavy plugins, missing caching, crawlers and badly written cron jobs. Fix the cause before you pay for more resources.""",

    "How to turn on SSH access and add a key": """## Good practice

Use keys instead of passwords, protect the private key with a passphrase and remove old keys. Switch SSH off when you do not use it.

## Useful commands for WordPress

Developers use SSH to run WP-CLI, Git and Composer. If you are not a developer you almost certainly do not need it.""",

    "How to restore deleted files from the Trash": """## Act quickly

Restore a deleted file as soon as you notice, before anyone empties the Trash. Check where it went back to and test the website.

## For older deletions

Use a backup. Download it, extract it and upload only the files you need so you do not overwrite newer work.""",

    "How to block an IP address from your website": """## When to block

Block an address that keeps trying to log in, scrapes your content or sends bad requests. Check your access statistics to see the address first.

## Better long-term protection

Blocking one address stops one source only. Keep WordPress updated, use strong passwords and consider a login protection plugin for repeated attacks.""",

    "How to create and use a subdomain folder for testing": """## Keep tests separate

A separate test area keeps experiments away from visitors and from search engines. Use a different database for it so a test never changes live content.

## Clean up

Delete test areas when you finish: they take space and can leak unfinished work if left open.""",
}
