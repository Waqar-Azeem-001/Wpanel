# N1: WH Agent

The agent is the program on a native node that does what WHPanel asks. **Django never uses it as a remote shell**: WHPanel can only queue the
commands in a fixed catalogue, each with typed parameters, and the agent refuses anything else on its own side too.

## What is built and what is not

| Part | State |
|---|---|
| Command catalogue and parameter rules (`apps/agent/catalog.py`) | Built, tested |
| Signed envelopes, replay guard (`envelope.py`) | Built, tested |
| Job queue, leases, retries, timeouts, results, audit (`models.py`, `services.py`, `views.py`) | Built, tested |
| Python reference agent against a fake machine (`reference_agent.py`) | Built, tested (the "machine" is a dictionary; nothing real is touched) |
| Go agent (`agent/main.go`) | Written in Go (`agent/`) for every command of this stage, checked against the Python reference by golden scenarios and against live Django end to end on a pretend machine. **The real-machine paths have never run on a real node** (see agent/README.md) |
| Real work on a node (users, web server, PHP-FPM, databases) | Not started: N3 and N4 |
| Per-node certificate / mutual TLS, one-time enrolment | Not started: N2. Until then a node is identified by a long random token (only its hash is stored). |
| Staff screens for jobs | Not started |

## Protocol

The agent connects **out** to WHPanel (ADR 16), over HTTPS.

* `POST /api/agent/v1/poll/` with `Authorization: Agent <token>` and `X-Agent-Version: x.y.z`. Reply `{"job": null}` or `{"job": <envelope>}`.
  An agent older than the minimum gets **426** and no work.
* `POST /api/agent/v1/jobs/<id>/result/` with `{"ok": bool, "code": str, "message": str, "data": {}}`. Anything else is 400. Repeating the same
  report is harmless; a different outcome for a finished job is 409.

### Envelope

`{"payload": base64(bytes), "signature": base64(Ed25519 signature of those bytes)}`. The agent verifies the signature over the received bytes first and
only then parses them, so Python and Go need no shared canonical-JSON rules. Payload fields: `v` (1), `job`, `correlation_id`, `node`, `command`,
`params`, `issued_at`, `expires_at` (Unix seconds), `nonce`, `idempotency_key`.

The agent refuses: bad signature; wrong `v`; wrong `node`; dated more than 60 s in the future; expired; nonce under 16 characters or seen before
(the table must survive a restart); a command not in the catalogue; any unknown, missing or malformed parameter.

The agent holds only the **public** key, so a node that is taken over cannot forge commands.

### Commands

| Command | Parameters | Timeout | Re-run safe |
|---|---|---|---|
| CREATE_WEBSITE | account, domain, php_version | 180 s | yes |
| DELETE_WEBSITE | account, domain, keep_archive (yes/no) | 180 s | yes |
| CREATE_DATABASE | account, name | 60 s | yes |
| CHANGE_PHP_VERSION | account, domain, php_version | 60 s | yes |
| ISSUE_SSL | account, domain, method (http-01/dns-01) | 300 s | yes |
| CREATE_MAILBOX | account, domain, local_part, quota_mb, password (secret) | 60 s | yes |
| CREATE_BACKUP | account, scope (full/files/databases) | 3600 s | **no** |
| RESTORE_BACKUP | account, backup_id, scope | 3600 s | **no** |

Patterns: account `^[a-z][a-z0-9]{2,15}$`; domain a lower-case host name; db name `^[a-z][a-z0-9_]{2,47}$`; local part `^[a-z0-9][a-z0-9._-]{0,63}$`;
backup id `^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$`; PHP `7.4 8.0 8.1 8.2 8.3 8.4`; secret 12 to 128 printable characters; integers have fixed ranges.
Booleans are not accepted as integers. There is no free-text, path or argument-list field. The Go agent must run programs with an argument **list**, never a shell string.

## Delivery rules

* **Idempotency**: `dispatch(..., idempotency_key=...)` returns the existing job for the same key and command; the same key with different parameters is refused.
* **Leases**: a polled job is leased for its timeout plus 30 s. If the agent never reports, an idempotent command goes back to the queue (up to 3 attempts, with a
  **new nonce** so the replay guard accepts the second delivery). A non-idempotent one (backups) is marked *timed out, outcome unknown* and is **never** run again on a guess.
* **Expiry**: a command is signed valid for 10 minutes; one not started by then becomes *expired*.
* **Versions**: protocol 1; minimum agent 0.1.0; each command can raise its own minimum, and it stays queued until the agent is upgraded.
* **Secrets**: a password is carried in the signed command, never in the audit trail, and is replaced by `***` in the stored job as soon as it finishes.
  While queued it is stored in the job row, so the database must be protected like the rest of WHPanel's secrets. (Encrypting it to the node's own key arrives with N2.)
* **Audit and correlation**: every job records who queued it, the correlation ID (carried to the agent and back) and the final outcome.

## Setting up

1. Generate a signing key: `python -c "import django,os;os.environ['DJANGO_SETTINGS_MODULE']='config.settings.base';django.setup();from apps.agent import envelope;print(envelope.generate_key())"`
   and put it in `.env` as `AGENT_SIGNING_KEY`. Without it a key derived from `SECRET_KEY` is used, which is acceptable for development only.
2. `from apps.agent import services; node, token = services.issue_token(server)` gives the node's token once. Give the agent the token, its node id (the
   server's id), the WHPanel address and `envelope.public_key_b64()`.

## Tests

`apps/agent/tests/test_agent_protocol.py` (42 tests): every off-list command and injection attempt, tampered/forged/foreign-key/expired/future/replayed envelopes,
the full round trip with the reference agent, idempotency, ordering and node isolation, token checks, structured results, lease re-delivery, the
no-second-backup rule, old-agent handling, cancellation, and secret scrubbing.
