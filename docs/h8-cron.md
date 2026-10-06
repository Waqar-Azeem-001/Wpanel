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

---

# H8b: Logs, FTP accounts and IP blocking (rest of H8)

## What was built
- **Contract:** three new capabilities, `logs` (`read_log`, `clear_log`), `ftp` (options, list, create, set password, delete) and
  `access` (list, add, delete IP rules), all gated through the hosting-control gateway; the reads are allowlisted as read operations.
- **Logs** (`demo_tools.py`): the error and access logs are real files in the account's `logs` folder (visible in the file manager),
  filled with sample lines on first use. The viewer shows the last 50, 200 or 500 lines, filters by a word, and can clear a log
  (with confirmation). Only those two logs can be read; nothing outside the account.
- **FTP accounts:** login is `name@domain`; the folder must exist inside the hosting space; password 10 to 128 characters with upper
  and lower case letters and numbers (no repeated-character passwords); five accounts; stored only as a hash and never shown again;
  change password and delete. The screen shows the server, FTP and SFTP ports and recommends SFTP.
- **IP blocking:** an address or range, tidied; ranges wider than /16 (IPv4) or /48 (IPv6), loopback and multicast are refused;
  duplicates refused; fifty rules; and the screen refuses a block that would include the customer's own connection.
- Audit records login names, folders, rule ids and log kind, never passwords or log lines.
- **Redirects** (also listed in H8) already exist per domain in H5 (Domains screen), so they were not duplicated.
- Sample data: demo-shop has an FTP account and two blocked addresses; logs fill themselves.

## Verified
35 tests (`test_tools.py`): provider rules (log reads and filters, path tricks, FTP names, weak passwords, folder checks, limits,
IP tidying, broad ranges, duplicates), all three screens end to end, self-block refusal, audit without secrets, strangers 404, the
not-available page. Hosting and core suites pass. No real FTP server, log or firewall exists here; H14 connects a real provider.

## Deferred
Log download and rotation, FTP quota and bandwidth, allow-lists, country blocking, and the 375 / 768 px check.
