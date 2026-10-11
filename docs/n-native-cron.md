# Native scheduled jobs (cron)

The customer's Cron screen (`CronCapability`) now works on native nodes, through five allow-listed agent commands: `CRON_LIST`, `CRON_CREATE`, `CRON_UPDATE`, `CRON_DELETE`, `CRON_RUN`.

| Part | State |
|---|---|
| Rules: `php` + a script inside the website folder + plain arguments, five-field schedule, at most every 5 minutes, ten jobs, on/off, run now | Built (Python `apps/agent/node/cron.py`, Go `agent/internal/handlers/cron.go`), tested, **Python and Go give identical answers** (golden scenario) |
| How it runs: `/etc/cron.d/whp-<account>` lines are the fixed `wh-agent cron-run <account> <job>`; the agent starts PHP with `systemd-run` in the account's slice, as its user, sandboxed, for at most an hour | Built; **never run on a real node** (cron.d, systemd-run, the slice) |
| Suspended website: its jobs do not run; deleting the website removes its jobs | Built, tested |
| Panel adapter `NativeCron`, customer screen | Built, tested through the real adapter |

Not built: any command other than `php script`; shell scripts, Python, Node (the platform rule since the cPanel era); email of job output; run history beyond the last run. cPanel crontabs are imported by the migration where the line is a plain php script (docs/m-cpanel-migration.md).
