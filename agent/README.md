# wh-agent (Go)

The program that runs on a native node and does what WHPanel asks. Standard library only. It runs only commands from a fixed catalogue
(`spec/catalog.json`, generated from `apps/agent/catalog.py`), only if they carry WHPanel's Ed25519 signature, and never through a shell.

## Verification level (honest)
- Verified on a development machine: unit tests, 44 golden scenarios compared with the Python reference, 3545 validation cases, signed-envelope fixtures,
  and 8 end-to-end tests (Go binary <-> live Django, `apps/agent/tests/test_go_agent.py`) on a **pretend machine** (`--fake-machine`).
- **Never run on a real node:** `system.Real`, the run-as-user file worker (`internal/runas`), MariaDB, certbot, restic, Apache/PHP-FPM/Postfix/Dovecot control,
  quotas, systemd. `deploy/native-node/install-node.sh` is likewise untested.

## Build and test
    cd agent
    go vet ./... && go test ./...
    GOOS=linux GOARCH=amd64 go build -o wh-agent ./cmd/wh-agent

After changing `apps/agent/catalog.py` run `python manage.py export_agent_spec`; a test fails if the files in `spec/` are stale.

## Use
    wh-agent enroll --panel https://panel.example.com --token TOKEN --capabilities web,php,database,ssl,mail,backup,metrics
    wh-agent check
    systemctl enable --now wh-agent       # see deploy/native-node/wh-agent.service
