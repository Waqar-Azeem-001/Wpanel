# H9: Backup and restore

**Source:** next-generation roadmap, H9 ("a successful backup job alone is not proof of recoverability"). Local only.

## What was built
- **Contract:** `BackupInfo` gained `source` (manual, scheduled, before restore) and the check result; new `BackupOptions` (kinds,
  retention, schedules, current schedule). Operations: `backup_options`, `list_backups`, `create_backup`, `verify_backup`,
  `restore_backup`, `delete_backup`, `set_backup_schedule` (the first two are read operations).
- **Demo provider** (`demo_backups.py`), real archives kept beside the account:
  - Kinds: website files, databases, email, full account (those plus domains, DNS, SSL, PHP, cron, FTP and IP-block settings). The
    trash is never included.
  - Integrity and **tested restore**: a checksum of the archive and of every file inside it is recorded when a backup is made.
    **Test** recomputes the archive checksum, then does a real trial restore into a scratch folder: every file must match its
    recorded checksum, every database must open and pass SQLite's own integrity check, every settings file must parse, and the
    contents must match the manifest. A changed, incomplete, damaged or missing archive is marked failed. Nothing on the website is
    touched by the test.
  - **Off-server copy:** a switch; when on, every new backup is also written to a separate location (standing in for storage away
    from the server, which a native provider needs). If the local copy is lost or damaged and the off-server one is sound, the test
    and the restore use it and rebuild the local copy. A lost off-server copy is shown as "Missing".
  - Restore: refused unless the backup passes the check (nothing is changed); otherwise a safety copy of what will be replaced is
    taken first ("before restore"), then exactly the parts the backup holds are replaced. Archive names are validated, so a hostile
    archive cannot write outside the account.
  - Retention: the newest seven are kept. Schedule: off, daily or weekly full backups, taken when the list is next read.
- **Screen:** back-up-now choice, schedule, list with when, source, size, check state, and Check, Restore (with a confirmation) and
  Delete; an explanation that only a checked backup counts.
- Sample data: demo-shop has a checked full backup, a database backup and a weekly schedule.
- Audit records the backup id, kind and schedule.

## Verified
20 provider tests (what each kind holds, listing, integrity failures, restore of files, databases, email and settings, refusal of a
damaged or hostile archive, retention, delete, schedule) and 6 screen tests. Hosting and core suites pass.

## Deferred
Download of a backup, real remote storage and encryption of off-site copies (the demo uses a second folder), per-file or per-table restore, and the 375 / 768 px check.
