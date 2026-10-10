
# N10: Monitoring and controlled self-healing

Roadmap: monitor node/agent health, CPU/RAM/disk/load, network, web service, database, DNS, mail, backups, certificates and queues, in the states **Healthy, Warning, Critical, Offline**; only bounded,
proven-safe operations may repair anything automatically.

## What is built and what is not

| Part | State |
|---|---|
| One place that decides a node's state (`apps/agent/monitoring.py: evaluate`), used by the staff screens, alerts, placement and repairs | Built, tested |
| What a node reports (`apps/agent/node/metrics.py`): CPU, load, memory, disk, network errors, service states, mail queue, soonest certificate expiry | Built; **only run against made-up `/proc` files**, never on a real machine |
| Settling (two sightings in a row), state history, alert emails, reminders, recovery notice | Built, tested |
| Backups, certificates, queues, mail reputation, DNS in the verdict | Built (backup failures/staleness/restore test, certificate days, waiting commands, blacklist, PowerDNS reachability) |
| Staff **Monitoring** screen | Built, tested |
| Self-healing: restart a failed service from a short list, within strict limits | Built, tested against a pretend machine. **Switched off by default** (`SELF_HEALING_ENABLED`) because it has never run on a real node |
| Go agent sending these figures | Built in Go (`internal/handlers/metrics.go`) and tested on a pretend machine; never run on a real node |
| Metric history and graphs (Prometheus, trends) | **Not built.** Only state changes are kept. ADR 13 says to add Prometheus when there are more than a handful of nodes |
| An external uptime check | **Not built** (ADR 13 recommends one: WHPanel cannot notice that WHPanel itself is down) |
| Alert channels other than email (Telegram, WhatsApp, a pager) and an on-call rota | **Not built**; a business decision (ADR 13 open question) |
| DNS server health beyond "answers"; web checks from outside (does the site actually load) | Not built |
| Scheduling the sweep | A Celery beat task every minute: **only runs if Celery beat runs** |

## States

* **Offline**: the agent has not been heard from for 3 minutes (nothing else can be said).
* **Critical**: any check critical: CPU ≥ 98%, memory ≥ 97%, disk ≥ 95%, load ≥ 3 per core, mail queue ≥ 1000, certificates ≤ 3 days, any service reported down, a failed test restore, a mail node on a blacklist,
  a command waiting ≥ 30 minutes.
* **Warning**: any check at its warning level (CPU 90, memory 90, disk 85, load 1.5 per core, queue 200, certificates 14 days, a struggling service, a failed backup in the last day, no good backup for three days,
  backup storage not set up, waiting command ≥ 5 minutes, many network errors).
* A figure the node did not report is **unknown**: it never makes a node look fine or broken. (A node that stops sending figures but keeps its heartbeat will therefore look healthy: watch the "agent contact" and service lines.)
* New sites are placed on healthy or warning nodes only; critical and offline ones are skipped.
* The thresholds are in `monitoring.THRESHOLDS` and are starting points to be tuned on real nodes.

## Alerts

`MONITORING_ALERT_EMAILS` (comma separated). One email when a node settles into a worse state, one when it recovers, a reminder every six hours while it stays critical or offline. With no address set nothing is sent
(the screen says so in red). A failure to send is recorded and never stops monitoring.

## Self-healing: what it may and may not do

It may restart **only** these services: `httpd`, `mariadb`, `postfix`, `dovecot`, `rspamd`, `sshd`. It never deletes files, kills a customer's process, repairs a database, edits configuration or reboots.
All of these must be true before it acts, or it does nothing:

1. `SELF_HEALING_ENABLED` is on.
2. The node has been settled **critical** (two sightings), is **not in maintenance**, and no command is running on it.
3. The service is reported **down** (a struggling service is left alone).
4. That service was restarted fewer than 3 times in the last hour and not within the last 5 minutes.
5. For the web and SSH servers the configuration is **tested first**; a failing test stops the restart (restarting on a broken configuration would turn a problem into an outage).
6. After the restart the service must report active, or the attempt counts as failed.

After three restarts in an hour that did not fix it, it **gives up, records that once, emails "a person is needed"**, and does nothing more for that service. Every action is recorded (screen and audit trail). The list lives in one
place, `catalog.HEAL_SERVICES`, and the node refuses anything else, so a mistake in WHPanel cannot widen it.

**Do not turn it on before it has been proven on a real node** (restart each service by hand through the command, break each one on purpose and watch the loop, confirm the limits hold).

## Tests

`apps/agent/tests/test_monitoring.py` (27): figures from made-up `/proc`, unreadable figures left out, services asked per node type; every figure's warning and critical level (16 cases), service verdicts, unknown is
not fine; backups/queue/reputation in the verdict; settling, alerts, recovery, reminders, no-recipient and failing mail; platform checks; the repair (restart and record, off by default, not before settled, only on the list,
never in maintenance or while busy, spacing and the limit then giving up once, a failing configuration test, a service that does not come back, the whole loop from a heartbeat); the monitoring screen and node page.
