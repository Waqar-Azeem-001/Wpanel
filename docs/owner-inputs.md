# What we need from the owner to go live

Please send each item below. Nothing is deployed without your approval. Items 1 to 3 are needed first.

| # | Item | Why we need it | Your answer |
|---|---|---|---|
| 1 | The production domain (for example portal.yourdomain.com) and a staging address (staging.yourdomain.com) | Every emailed link, the secure (HTTPS) certificate and the DNS | |
| 2 | A server (VPS) we can install Docker on: provider, IP, SSH access for us. And how HTTPS should be done: certificate on the server (Let's Encrypt), or through a load balancer / Cloudflare | Where the system runs | |
| 3 | A working WHM API token for your hosting server (and its address), with permission to create, suspend, unsuspend, terminate and back up accounts | Last live test on your server was refused (HTTP 403). Account creation cannot be verified without it | |
| 4 | Email service for sending (SMTP or an email API) and the sending domain, plus access to add DNS records (SPF, DKIM, DMARC) | Invoices and notifications reaching inboxes, not spam | |
| 5 | Domain registrar account and API details | Domain registration and renewal | |
| 6 | The payment methods to show customers: bank account details, JazzCash, etc., and the wording for instructions | Payments are recorded by hand | |
| 7 | Where backups should be stored (outside the server) and who should be alerted if one fails | Recovery if the server is lost | |
| 8 | Email for the first Super Admin account | Your login | |
| 9 | Optional: an error-tracking account (Sentry) | Be told when something breaks | |

Questions for you: Is the WordPress storefront needed on launch day? Who will record payments each day?
