"""Hosting tools: files, FTP, databases, PHP, cron, logs, traffic, resources and SSH."""
C = "hosting-tools"

ARTICLES = [
    (C, "How to use the File manager", "Upload, create, edit, move, extract and download files with the File manager in your hosting panel, and restore deleted files from the Trash.", True, """The **File manager** lets you work with your website's files in the browser, without extra software.

## Open it

Open **Websites**, choose your hosting plan and open **Files**.

## Everyday tasks

- **Upload**: drag files into the window or use the upload button.
- **New folder / new file**: use the buttons at the top.
- **Edit**: open a text or code file and save your change.
- **Rename and move**: select the file and use the menu.
- **Extract**: open a .zip file and extract it into a folder.
- **Download**: select files and download them.

## The Trash

A deleted file goes to the **Trash**. Open the Trash to restore it, or empty it to free the space.

## Where is my website?

Your website's files are in the folder named after your domain, or in `public_html` for the main one.

## Frequently asked questions

### Is there a file size limit for uploads?
Large files may be limited. For big uploads use FTP or upload a zip and extract it.

### Can I undo a deletion?
Yes, from the Trash, until you empty it.

### Why can I not edit a file?
It may be a binary file such as an image. Download and replace it.
"""),

    (C, "How to create an FTP account and upload files", "Create an FTP account, find the host and port, and upload your website files with FileZilla or another FTP program.", False, """**FTP** (and secure SFTP) is the classic way to upload many files to your website.

## Create an account

1. Open your hosting plan > **FTP accounts**.
2. Press **Create** and write a user name and a strong password.
3. Choose the folder the account may reach.
4. Save.

## Connect with an FTP program

1. Open FileZilla or another FTP program.
2. Enter the **host** and **port** shown on the FTP page, your FTP user name and password.
3. Use the secure option where it is offered.
4. Drag your files into your website's folder.

## Give a developer access safely

Create a separate FTP account for the developer, limited to the website's folder, and delete it when the work is done. Never share your hosting password.

## Frequently asked questions

### FTP or SFTP?
SFTP is encrypted and recommended. Use it when your plan offers it.

### Why can I not connect?
Check the host, port and password, and that your firewall allows the connection.

### Where do files go?
Into the folder you chose for the account.
"""),

    (C, "How to create a database and a database user", "Create a MySQL database and user, give the user access and find the details a website needs to connect.", False, """Websites such as WordPress store their content in a **database**.

## Create them

1. Open your hosting plan > **Databases**.
2. Create a **database** and a **user** with a strong password.
3. Give the user **access** to the database.

## Connect a website

A website needs four details: the database name, the user name, the password and the host (usually `localhost`). Put them in the website's settings file.

## WordPress websites

A WordPress website created in the panel already has its own database and user. Do not delete them.

## Frequently asked questions

### Why is the database name prefixed?
Names carry your account name so they stay unique on the server.

### How do I change a database password?
Set a new password for the user, then update the website's settings file with it.

### How many databases can I have?
The page shows your plan's limit.
"""),

    (C, "How to browse and edit database tables in the panel", "Open a database, browse its tables and rows and edit a value right in the panel, with care and a backup first.", False, """You can look inside a database and fix a value without installing any tool.

## Open it

1. Open your hosting plan > **Databases**.
2. Press **Open** on the database.
3. Choose a table to see its columns and rows.

## Edit a row

Open the row, change the value and save. This is useful to fix a wrong site address or to reset a setting.

## Be careful

- Create a backup first. See [[How to create and download a backup]].
- Change only what you understand: a wrong value can break your website.
- Do not edit WordPress serialized settings by hand.

## Frequently asked questions

### Can I run my own SQL?
The panel focuses on browsing and editing rows safely.

### Why can I not see a table?
It may belong to another database or your user lacks access.

### Does editing a row affect my website at once?
Yes, immediately.
"""),

    (C, "How to change your PHP version", "Choose the PHP version for your website, why newer is better and what to do if a website breaks after the change.", True, """PHP is the language that runs WordPress and many other websites. Using a current version makes your website faster and safer.

## Change the version

1. Open your hosting plan > **PHP**.
2. Choose your domain.
3. Pick a version from the list. Versions marked as end of life no longer get security fixes.
4. Save.

## After you change it

Open your website and click around. If something breaks, set the previous version again and look for outdated plugins or themes.

## Which version should I choose?

The newest version your website supports. Update plugins and themes first so they work with it.

## Frequently asked questions

### Will my website go offline?
Only if the code is not compatible. Switch back to fix it quickly.

### Can each website use a different version?
The setting is per domain.

### Where do I see errors?
In the error log. See [[How to read the error log of your website]].
"""),

    (C, "How to raise PHP memory limit, upload size and execution time", "Fix 'allowed memory size exhausted', failed uploads and timeouts by raising PHP limits safely in the panel.", False, """Some errors come from PHP limits that are too low for your website.

## Common signs

- "Allowed memory size exhausted".
- Uploads fail for large files.
- Imports or backups stop with a timeout.

## Change the limits

1. Open your hosting plan > **PHP**.
2. Choose your domain.
3. Change **Memory limit** (for example 256M or 512M), **Upload size** and **Maximum execution time**.
4. Save.

## Sensible values

- Memory limit: 256M for most WordPress websites, 512M for heavy ones.
- Upload size: slightly more than your largest file.
- Execution time: 120 to 300 seconds.

## Let the AI Agent do it

Ask the AI Agent to raise a limit: it explains the steps and, if you want, prepares the change for you to approve.

## Frequently asked questions

### Why not set everything to the maximum?
High values let a broken script use too many resources.

### Do I need to restart anything?
No. The change applies right away.

### The limit did not change?
Check you chose the right domain and clear any website cache.
"""),

    (C, "How to switch PHP extensions on or off", "Enable the PHP extensions your website needs, such as imagick, intl or mbstring, and disable the ones it does not.", False, """PHP extensions add features. Some plugins need specific ones.

## Where

Open your hosting plan > **PHP** and open the **Extensions** list for your domain.

## Steps

1. Find the extension, for example `imagick`, `intl` or `mbstring`.
2. Switch it on (or off).
3. Save.

## When you need to change them

A plugin or theme tells you "extension X is missing". Switch that extension on.

## Frequently asked questions

### Can I switch on any extension?
Only those your server offers.

### Will extra extensions slow my website?
Slightly. Switch on only what you need.

### Does it depend on the version?
Yes. The list changes with the PHP version.
"""),

    (C, "How to set up a cron job", "Run a PHP script automatically on a schedule with cron jobs, and test it with a manual run.", False, """A **cron job** runs a script at the times you choose, for example to send reports or clean old data.

## Create one

1. Open your hosting plan > **Cron jobs**.
2. Press **Add**.
3. Choose the PHP script inside your account.
4. Pick a ready schedule (every hour, every day, every week) or write your own timing.
5. Save.

## Test it

Use **Run now** in the list to run the job once and check the result.

## Rules for safety

Only scripts inside your account are accepted, and a job cannot run more often than your plan allows.

## Frequently asked questions

### What is WordPress cron?
WordPress has its own task system that runs when someone visits. For exact timing use a real cron job.

### How do I stop a job?
Delete or edit it from the list.

### Where is the output?
Errors appear in your error log.
"""),

    (C, "How to read the error log of your website", "Find out why your website shows an error by reading the newest lines of its error log, and understand common messages.", False, """The **error log** records problems PHP and the server meet. It is the first place to look when a page fails.

## Open it

Open your hosting plan > **Logs** and choose the error log. The newest lines are at the bottom.

## How to read a line

A line shows the time, the kind of error, a message and the file with its line number. The file path often shows which plugin or theme is the cause, for example `/wp-content/plugins/name/`.

## Common messages

- **Allowed memory size exhausted**: raise the memory limit. See [[How to raise PHP memory limit, upload size and execution time]].
- **Call to undefined function**: a plugin needs a missing PHP extension or a newer PHP.
- **Permission denied**: a file has wrong permissions.

## Frequently asked questions

### Can I clear the log?
Yes. Clear it, reproduce the problem and read only the new lines.

### Why is the log empty?
There may be no errors, or logging is limited by your plan.

### Can the AI Agent read it?
Yes. Ask it to show your error log.
"""),

    (C, "How to see your website traffic and visitors", "See visitors, page views and your top pages from the statistics of your own website, without adding a tracking script.", False, """Traffic statistics show how many people visit your website and what they open.

## Open them

Open **Websites**, choose your website and open **Website care**, then **Traffic**. A detailed statistics report is also available from the hosting plan's tools.

## What you can learn

- Visitors and page views.
- Your most visited pages.
- Where visitors come from.

## Why server statistics?

They come from your server's own log, so they need no script on your pages and are not blocked by ad blockers. Numbers can differ from tools like Google Analytics.

## Frequently asked questions

### Are bots counted?
Server statistics may include some automated visits.

### Can I see live visitors?
The data is updated regularly, not second by second.

### Does it slow my website?
No.
"""),

    (C, "How to check your resource usage (CPU, memory and processes)", "See how much CPU, memory, processes and disk activity your hosting uses, with a 24-hour history, and what to do when you hit a limit.", False, """The **Resource use** page shows how your website uses the plan's allowance right now and over the last 24 hours.

## What the numbers mean

- **CPU**: processing power used.
- **Memory**: RAM used.
- **Processes**: how many tasks run at the same time.
- **Input/output**: how fast files are read and written.

## When you reach a limit

Pages become slow or show errors for a short time. To reduce the load:

- Switch on caching with **Faster pages**.
- Remove heavy plugins.
- Block bad traffic.
- Move to a bigger plan.

## Frequently asked questions

### Does a spike mean a problem?
Short spikes are normal. Frequent limit hits are the warning sign.

### Where is the history?
On the same page, as a chart of the last 24 hours.

### What is a sensible limit?
Depends on the plan. The page shows yours.
"""),

    (C, "How to turn on SSH access and add a key", "Switch the shell on for your hosting plan, add a public key and connect securely, with safety advice.", False, """**SSH** gives advanced users a secure command line on the hosting.

## Switch it on

1. Open your hosting plan > **SSH access**.
2. Switch the shell **on**.

## Add a key

Where the server allows keys, add your **public** key. A public key is safe to share; a private key must never leave your computer. The panel refuses private keys.

## Connect

Use the host, port and user name shown on the page.

## Switch it off

Switch SSH off again when you do not need it.

## Frequently asked questions

### Do I need SSH?
Most people do not. Developers use it for tools like Git and WP-CLI.

### How do I make a key?
Use `ssh-keygen` on your computer and paste the public part.

### Is SSH safe?
Yes with keys and a strong password. Keep it off when unused.
"""),

    (C, "How to restore deleted files from the Trash", "Bring back files you deleted by mistake from the Trash in the File manager, and empty the Trash to free space.", False, """A file you delete in the File manager is moved to the **Trash** first.

## Restore a file

1. Open your hosting plan > **Files**.
2. Open the **Trash**.
3. Select the file and press **Restore**. It returns to where it was.

## Empty the Trash

Press **Empty** to delete everything for good and free the space.

## If it is not in the Trash

It may have been deleted a long time ago or the Trash was emptied. Restore it from a backup. See [[How to restore a backup]].

## Frequently asked questions

### How long are files kept?
Until you empty the Trash.

### Do deleted emails go here?
No. Email has its own Trash in webmail.

### Does the Trash use disk space?
Yes. Empty it to free space.
"""),

    (C, "How to block an IP address from your website", "Block a visitor or a bot by IP address to stop abuse, and remove the block later.", False, """If one address keeps attacking or overloading your website, you can block it.

## Steps

1. Open your hosting plan > **Advanced** and choose **IP blocking**.
2. Enter the address you want to block.
3. Save.

## Remove a block

Open the same list and delete the rule.

## Good to know

- Blocking your own address locks you out of the website.
- Attackers often change addresses, so combine blocking with updates and a malware scan.

## Frequently asked questions

### Can I block a range?
Enter the rule format the page shows.

### Does it block email?
It applies to your website.

### How do I find the address?
Look at the traffic statistics or the access log.
"""),

    (C, "How to create and use a subdomain folder for testing", "Create a test area on a subdomain, upload a copy of your website there and keep it private with a password.", False, """A separate test area lets you try changes without touching your live site.

## Create it

1. Add a subdomain such as `test.yourdomain.com`. See [[How to add a subdomain]].
2. Upload or install the website into its folder.

## Keep it private

Protect the folder with a password so only you can open it. See [[How to protect a folder with a password]]. Also ask search engines not to index it.

## Easier option

For WordPress, use **Staging website** which does all this for you. See [[How to create a staging website to test changes safely]].

## Frequently asked questions

### Does a subdomain cost extra?
No, but it uses the plan's resources.

### Can I delete it later?
Yes. Delete the subdomain and its folder.

### Is it indexed by Google?
Not if you block it with a password.
"""),
]
