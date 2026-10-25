# Off-site backups to Backblaze B2: one-time setup and the quarterly drill

What you get: every nightly backup is also stored at Backblaze, in a bucket that the server **can add to but cannot read, change or delete**, and that **cannot be erased early even by you** (Object Lock). Pre-deploy database dumps go there too, encrypted to your own GPG key first.

The code for all of this is already in the panel. This page is what **you** do in Backblaze, and what to give to the server afterwards. Nothing is switched on until the settings in section 3 are in `.env`.

## 1. Backblaze: the bucket

1. Make a Backblaze B2 account (two-factor on). Choose the **EU Central** region (the server is in Germany).
2. **Create a bucket**: name it for example `whpanel-backups-<something unique>`. **Files in bucket: Private.** **Object Lock: Enable** (this can only be set when the bucket is made).
3. **Default retention** (Bucket settings > Object Lock): mode **Governance**, **30 days**. (Governance lets only the account owner's master key shorten it; the server's key can never. Choose Compliance only if even you must not be able to delete early.)
4. **Lifecycle rules**: use *Bucket settings > Lifecycle Settings > Use custom rules* and enter these four (or send this JSON to `b2_update_bucket`):
   ```
   whpanel/daily/       hide after 31 days, delete 1 day later     (30 daily copies)
   whpanel/predeploy/   hide after 31 days, delete 1 day later
   whpanel/check/       hide after 31 days, delete 1 day later     (the little notes the check command writes)
   whpanel/monthly/     hide after 366 days, delete 1 day later    (12 monthly copies)
   ```
   ```json
   [{"fileNamePrefix": "whpanel/daily/", "daysFromUploadingToHiding": 31, "daysFromHidingToDeleting": 1},
    {"fileNamePrefix": "whpanel/predeploy/", "daysFromUploadingToHiding": 31, "daysFromHidingToDeleting": 1},
    {"fileNamePrefix": "whpanel/check/", "daysFromUploadingToHiding": 31, "daysFromHidingToDeleting": 1},
    {"fileNamePrefix": "whpanel/monthly/", "daysFromUploadingToHiding": 366, "daysFromHidingToDeleting": 1}]
   ```
   The 30-day default lock is the minimum life of every file; the rules above then decide when each folder's files finally go. The server's key has no delete permission, so **this is the only thing that removes old files**.

## 2. Backblaze: two keys

*App Keys > Add a New Application Key*, both limited to **this one bucket**:

| Key | Capabilities | Where it goes |
|---|---|---|
| `whpanel-server-write` | **writeFiles** only (nothing else) | The server's `.env` (section 3) |
| `whpanel-restore-read` | **listFiles**, **readFiles**, **listBuckets** | **Not on the server.** Your password manager. Typed into the command line only during a restore or a drill |

Copy each key's secret when it is shown; Backblaze shows it once.

## 3. The server's settings

Send me (or put in `/srv/whpanel/.env` yourself) these lines. The endpoint and region are shown on the bucket's page:

```
PANEL_BACKUP_S3_BUCKET=<the bucket name>
PANEL_BACKUP_S3_ENDPOINT=https://s3.<region>.backblazeb2.com
PANEL_BACKUP_S3_REGION=<region, for example eu-central-003>
PANEL_BACKUP_S3_ACCESS_KEY=<keyID of whpanel-server-write>
PANEL_BACKUP_S3_SECRET_KEY=<applicationKey of whpanel-server-write>
PANEL_BACKUP_S3_PREFIX=whpanel
```
(`PANEL_BACKUP_S3_PRUNE` stays off: the key cannot delete, so the lifecycle rules do the pruning.) Then recreate the containers so they read it, and test:

```
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec web python manage.py offsite_check
```
Expected: `The off-site copy works: whpanel/check/check-<time>.txt`. The same check does not need the read key. After the next 01:30 the file `whpanel/daily/panel-postgresql-<date>-full.bak.enc` is in the bucket (look in the Backblaze file browser). The Health panel's "Backups" line stops saying "none copied off this server".

## 4. Your GPG key for the pre-deploy dumps

On **your own computer** (not on the server):
```
gpg --full-generate-key                     # RSA 4096 or ECC; give it a strong passphrase; name it "WHPanel backups"
gpg --list-keys --fingerprint "WHPanel backups"      # note the 40-character fingerprint
gpg --armor --export "WHPanel backups" > whpanel-backup-public.asc
gpg --gen-revoke "WHPanel backups" > whpanel-backup-revocation.asc    # keep this too
```
Keep the **private key** and the passphrase safe and offline (a second copy on a USB stick in a safe place): `gpg --armor --export-secret-keys "WHPanel backups" > private.asc` and store that file offline; delete it from the computer afterwards if you wish.

On the **server**, as root, import only the public half and tell the deploy script who to encrypt for:
```
gpg --import whpanel-backup-public.asc
sed -i 's/^WHPANEL_GPG_RECIPIENT=.*/WHPANEL_GPG_RECIPIENT=<the 40-character fingerprint>/' /etc/whpanel/deploy.env
gpg --list-secret-keys        # must print nothing: this server has no private key
```
From the next deploy the log says "a copy, encrypted to <fingerprint>, is stored off the server", and `whpanel/predeploy/predeploy-<time>.dump.gpg` appears in the bucket.

## 5. What to watch

- The deploy log line above (a WARNING there means the off-site copy did not go; the deploy still goes on).
- The Health panel on the dashboard: "Backups" must be green and must not say "none copied off this server".
- In Backblaze, once a month, look that `daily/` has about 30 files and `monthly/` one per month.

## 6. The quarterly restore drill (about 15 minutes)

Goal: prove that what is in B2 can be downloaded, opened and restored, **without touching the live panel**. Do it on the server as root. The script `restore-drill` starts a throwaway PostgreSQL in a container with **no network**, so it cannot reach the live database.

1. **Download the newest daily bundle** with your *read* key (typed, never saved):
   ```
   cd /srv/whpanel
   docker compose -f docker-compose.yml -f docker-compose.prod.yml exec \
     -e PANEL_BACKUP_S3_ACCESS_KEY=<read key id> -e PANEL_BACKUP_S3_SECRET_KEY=<read key> \
     web python manage.py offsite_fetch --folder daily --latest --to /app/host_backups/drill
   ```
   It prints `Downloaded whpanel/daily/panel-postgresql-<date>-full.bak.enc (<size> bytes) to /app/host_backups/drill/...` (that folder is `/srv/backups/drill` on the server).
2. **Restore it into the throwaway database and compare:**
   ```
   bash /srv/whpanel/deploy/server/restore-drill --bundle /srv/backups/drill/<file>
   ```
   The script decrypts the bundle with the panel's own key, restores `database.dump` into the throwaway database, counts the rows of **every table** there and in the live database (the live session is read-only), and prints the differences. **Pass** = `0 with FEWER rows live`, `0 missing in live`, and the key tables (`accounts_user`, `clients_client`, `billing_invoice`, `orders_order`) not empty. Tables with *more* rows live are normal: the panel kept working after the backup.
3. **Do the same for a pre-deploy dump.** Download the newest one: repeat step 1 with `--folder predeploy --latest`. Copy the `.gpg` to **your own computer**, decrypt there (`gpg --decrypt predeploy-<time>.dump.gpg > predeploy-<time>.dump`; it asks for your passphrase), copy the `.dump` back into `/srv/backups/drill/` and run `bash /srv/whpanel/deploy/server/restore-drill --dump /srv/backups/drill/predeploy-<time>.dump`. This proves that your private key really opens the dumps. Then **delete the decrypted `.dump` from your computer**.
4. **Clean up:** `rm -rf /srv/backups/drill` on the server. (The script already removes its throwaway container and work files.)
5. **Write the result** in the table at the end of [backup-recovery.md](backup-recovery.md) (date, bundle used, pass or fail, anything odd).
6. If a step fails, treat it as an incident: the backups are not good until the drill passes. The most common causes: the wrong `CREDENTIALS_ENCRYPTION_KEY`, the write key used instead of the read key, or the GPG private key not matching the fingerprint in `/etc/whpanel/deploy.env`.

Once a year, also do the full "server lost" rehearsal from backup-recovery.md section 4 on a spare server.
