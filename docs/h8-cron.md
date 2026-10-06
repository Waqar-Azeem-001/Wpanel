# H8a: Cron jobs (first part of H8)

**Source:** next-generation roadmap, H8 ("Cron must not become an unrestricted privileged command interface"). Local only.

## What was built
- **Contract:** `CronJobInfo` gained last run time, status and output; new `CronOptions` (job limit, minimum interval, schedule
  presets) and two operations, `cron_options` (read) and `run_cron_job`.
- **Demo provider** (`demo_cron.py`):
  - Never a free shell: only `php <script>.php [plain arguments]`; the script must be inside the account (no leading slash, no `..`);
    arguments may not contain shell symbols (`; & | > < $` and backticks are refused); command length is capped.
  - Schedule: five valid fields (lists, ranges, steps checked against each field's range); nothing more often than every 5 minutes.
  - Ten jobs per account, duplicates refused, jobs can be switched off without deleting them, "run now" records a sample result.
- **Screen:** add form with six common schedules or a custom one, job list with a friendly schedule name, last run and result, an
  on/off switch, run now, edit dialog and delete with a confirmation.
- Audit records the script path (never the arguments or output).

## Verified
Provider rules (valid and invalid schedules, command attacks, limits, duplicates, run), the screen end to end, errors as messages,
audit, strangers 404, not-available page. Hosting and core suites pass.

## Not yet in H8
Error and access log viewer, FTP/SFTP accounts, IP controls and the hosting-level redirects screen. These are separate slices.
