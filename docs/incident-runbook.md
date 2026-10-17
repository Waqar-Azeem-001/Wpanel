# Incident runbook: when something goes wrong

Keep this open, work top to bottom, write down the time of each step. Panel server: `ssh -i ~/.ssh/whpanel_deploy root@62.238.96.50`, code in `/srv/whpanel`, compose command:
`docker compose -f docker-compose.yml -f docker-compose.prod.yml` (called `C` below).

## 0. First 15 minutes

1. **Do not delete anything.** Logs and files are the evidence.
2. **Stop new damage.** Turn on maintenance mode (Setup > Brand > Maintenance; staff can still sign in), or on the server `C stop web worker beat` (the site goes down, data stays).
3. **Tell the owner and one technical person.** Say what you saw and when.
4. **Switch off the AI Agent's changes** (Setup > AI Agent > "Changes through the Agent") if the Agent may be involved.
5. **Look**: Setup > Utilities > Audit log (who did what, from which address), `C logs --since 2h web worker`, `last -a | head`, `docker ps`.

## 1. Rotate the secrets (do all of them if you cannot tell what leaked)

| Secret | Where | How | What it affects |
|---|---|---|---|
| **WHM API token** | WHM > Manage API Tokens; WHPanel Setup > Servers | Revoke the old token first, make a new one (reseller with limited rights, whitelist the panel IP), paste it in Setup > Servers, press Test connection | Hosting actions stop until the new one is saved |
| **Django `DJANGO_SECRET_KEY`** | `/srv/whpanel/.env` | New long random value, `C up -d` | Everyone is signed out; password-reset and verification links in old emails stop working |
| **`CREDENTIALS_ENCRYPTION_KEY`** | `.env` | Only if it leaked. **Changing it makes the stored provider passwords unreadable**: re-enter the email provider, registrar, server tokens and payment secrets, and keep the new key in the password manager. Old encrypted backups need the OLD key | Provider logins |
| **Database password** | `.env` `POSTGRES_PASSWORD` and `DATABASE_URL`, plus `ALTER USER whpanel PASSWORD '...'` inside the db container | `C exec db psql -U whpanel -c "ALTER USER whpanel PASSWORD 'new';"` then edit `.env`, `C up -d` | Short restart |
| **Redis password** | `.env` `REDIS_PASSWORD` and `REDIS_URL` | `C up -d` | Queued jobs may be lost |
| **Storefront credentials** | Setup > External storefronts > Rotate | Each storefront gets a new secret; paste it into that WordPress site | The WordPress catalogue stops until updated |
| **Payment webhook secrets** | Billing > payment methods / provider | Rotate at the gateway and in WHPanel | Online payments |
| **Anthropic API key** | `.env` `ANTHROPIC_API_KEY` | Revoke in the Anthropic console, set a new one | The Agent's Claude mode |
| **SSH keys** | `/root/.ssh/authorized_keys` | Remove the key that may be lost; GitHub deploy key (`DEPLOY_SSH_KEY`) too | Deploys |
| **Staff passwords and 2FA** | Setup > Users | Reset passwords; a Super Admin can reset someone's two-factor sign-in | That person |
| **Cloudflare, registrar, GitHub, VPS provider, email** | Their own sites | Change the password, end all sessions, re-do 2FA | |

After rotating, sign everyone out: `C exec web python manage.py shell -c "from django.contrib.sessions.models import Session; Session.objects.all().delete()"`.

## 2. If the data is damaged or the server is lost

Backups (see `docs/operations-runbook.md`): the nightly encrypted `panel-postgresql-*.bak.enc` (newest 4) in the `panel_backups` volume and, when set, in the off-site bucket; plus the plain dumps `/srv/backups/whpanel-before-*.sql.gz` taken before each deploy.

1. New or cleaned server: Docker, the repo (`git clone`), `.env` from the password manager.
2. `C up -d db redis` then restore (or, with the panel running, Setup > Settings > Storage and backups > Restore, as a Super Admin):
   - from a pre-deploy dump: `gunzip -c whpanel-before-X.sql.gz | C exec -T db psql -U whpanel whpanel`
   - from an encrypted panel backup: `C exec web python manage.py shell -c "from apps.core import crypto; open('/tmp/p.dump','wb').write(crypto.decrypt_bytes(open('/app/panel_backups/FILE','rb').read()))"` then `pg_restore --clean --if-exists -h db -U whpanel -d whpanel /tmp/p.dump` (needs the encryption key).
3. `C up -d --build`, `C exec web python manage.py migrate`, open `/api/v1/health/`, sign in as staff, open an invoice and a ticket.
4. **Prove the restore with a drill once a month** on another machine: a backup that has never been restored is a hope, not a backup.

## 3. If a customer's website is hacked
Suspend nothing yet. Website care > Malware scan, move flagged files aside (quarantine), change the hosting and WordPress passwords, update WordPress, plugins and themes, check admin users. If the whole server is affected, treat it as section 2 for the hosting server too.

## 4. Telling customers
Within a day of a confirmed leak of customer data: email the affected customers (what happened, what data, what we did, what they should do: change the password, watch for phishing). Keep it short and honest. Keep a copy.

> "On <date> we found that <what>. <Whose data/what data> may have been seen. We have <what we did>. Please change your password and, if you reuse it elsewhere, change it there too. We will never ask for your password or a sign-in code by phone, chat or email. Questions: <contact>."

## 5. After it is over
Write down: timeline, root cause, what worked, what did not, what to change. Add the change to the roadmap. Rotate anything not yet rotated. Re-check the list in the security plan.
