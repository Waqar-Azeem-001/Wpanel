# Our Roundcube (step 1 of docs/email-platform.md)

1. On the webmail host: copy this folder, `cp .env.example .env`, fill `MAIL_SERVER_HOST` and `ROUNDCUBE_DES_KEY`, `docker compose up -d`.
2. Point `webmail.<our domain>` (DNS) at the host and put nginx/Cloudflare with TLS in front, proxying to `127.0.0.1:8025`.
3. Test with a throwaway mailbox only: open `https://webmail.<domain>/?_user=test@<domain>`, sign in, send and receive. Check IMAP 993 / SMTP 465 reach the cPanel server.
4. Only when that works: set `WEBMAIL_URL=https://webmail.<our domain>` in the panel's `.env` and redeploy. "Open webmail" then goes there with the address filled in. Remove the setting to go back to the hosting server's own webmail.

Customers type the mailbox password on the Roundcube page: cPanel offers no password-free mail login, and the panel never holds mailbox passwords.
