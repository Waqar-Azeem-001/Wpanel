# Operations Runbook (Phase 18)

Commands assume the project's `docker-compose.yml` (services `db`, `web`, `worker`, `beat`, `nginx`; volumes `pgdata`,
`private_media`, `media`). Nothing here is deployed yet; verify each step on staging before launch.

## 1. Error tracking (optional)

Set in the environment (off when `SENTRY_DSN` is empty): `SENTRY_DSN`, `SENTRY_ENVIRONMENT` (default `production`),
`SENTRY_TRACES_SAMPLE_RATE` (default `0`). Reports carry no personal data (`send_default_pii=False`). Code:
`config/error_tracking.py`, hooked up in `config/settings/prod.py`.

## 2. Backups

Two things must be backed up: the **database** and **private media** (ticket attachments; also `media`).

```
# Database (daily, keep at least 14 days)
docker compose exec -T db pg_dump -U whpanel -Fc whpanel > whpanel-$(date +%F).dump

# Private and public media
docker compose run --rm -v "$PWD":/backup web tar czf /backup/media-$(date +%F).tgz /app/private_media /app/media
```

**Owner decision needed:** where backups are stored (off the server, e.g. object storage) and who is alerted when one
fails. A backup kept only on the same server is not a backup.

## 3. Restore (test this on staging, not first in a crisis)

```
docker compose stop web worker beat
docker compose exec -T db dropdb -U whpanel whpanel && docker compose exec -T db createdb -U whpanel whpanel
docker compose exec -T db pg_restore -U whpanel -d whpanel < whpanel-YYYY-MM-DD.dump
docker compose run --rm migrate
docker compose start web worker beat
```
Restore media by extracting the tarball back into the volumes. Then check `/api/v1/health/`, sign in as staff and open
an invoice and a ticket attachment.

## 4. Monitoring

- Point an external uptime monitor at `GET /api/v1/health/` (checks the database; returns non-200 when it is down).
- Watch the staff console's automation status page for stalled scheduled jobs (Celery beat).
- Alert on: health check failing, backup job failing, Celery worker not running.

## 5. Deploy check notes

- `python manage.py check --deploy --settings=config.settings.prod` needs `SITE_URL` to be the real `https://` address.
- `SECURE_HSTS_PRELOAD` is intentionally not set. Enable it only when the domain and all subdomains are permanently
  HTTPS, since preload is hard to undo.
