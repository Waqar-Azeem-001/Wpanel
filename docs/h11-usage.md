# H11: Usage, Logs and Performance

**Source:** next-generation roadmap, H11. Local only (demo provider).

## What was built
- **Usage screen** (replaces the placeholder): the plan allowance for storage and this month's bandwidth, and "used / allowed" for
  databases, email accounts, extra domains (websites), subdomains, FTP accounts, cron jobs and backups kept. Each count and limit comes
  from the provider through the existing capabilities (database, email, domain, FTP, cron and backup limits), so nothing is duplicated.
- **Warnings:** anything at 80% or more turns amber, at 95% red, with a sentence and a link to the upgrade options; the colour is never
  the only signal because the figures are always written.
- **History:** a 7, 30 or 90 day chart of storage in use and of bandwidth per day (accessible SVG, with a table of the last days for
  screen readers and for exact figures).
- **Right now:** CPU, memory, running processes and disk activity against what the plan allows, refreshed on every visit.
- **Logs:** a button to the log viewer (H8).
- **Contract:** new `insights` capability (`usage_history`, `performance_stats`, both read-only) with `UsagePoint` and `PerformanceStat`.
- **Limits are enforced server-side:** the provider refuses a file write when the plan's disk is full ("Your disk space is full..."),
  whichever screen or API asks; databases, mailboxes, domains, FTP accounts, cron jobs and backups already refuse past their limits
  (earlier slices). The screens only display these limits.

## Verified
10 tests (`test_usage.py`): the history (one point per day ending today at today's figure, steady, only 7/30/90), performance figures
inside their limits, the disk-full refusal through the gateway, the screen with all sections, period switching with a safe fallback,
the warning for a nearly full account, the pending account message, strangers 404, the menu link. Hosting and core suites pass.

## Honest limits
Demo history and performance figures are generated from the account name and the date, so they look real and stay steady but are not
measurements. Only file writes check the disk limit; archive extraction, copies and database growth do not yet. Real measurements,
alerts by email when a limit is reached, and per-resource trend forecasts belong with the real provider (H14).
