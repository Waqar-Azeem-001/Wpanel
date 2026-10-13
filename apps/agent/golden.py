"""
Golden scenarios: the same commands run through WHPanel's Python reference agent on a pretend machine, recording EVERYTHING that happened (every file written, every program run in order, every result).
The Go agent runs the same scenarios and its record must be identical (``agent/internal/handlers/golden_test.go``). This is how the two implementations are kept honest: the Python one is the
specification and has its own tests; the Go one proves it does exactly the same, down to the order of the commands and the bytes of the configuration files.

Not covered here because they are random or need a real certificate authority: backup ids, certificate and DKIM keys, mail ids and dates (the Go tests cover those directly).
"""
import json
import shutil
import tempfile
from pathlib import Path

from .node.cron import Cron
from .node.databases import Databases
from .node.mariadb import FakeMariadb
from .node.servicectl import ServiceControl
from .node.sftp import Sftp
from .node.system import FakeSystem
from .node.website import HandlerError, Websites

SITE = {"account": "abcd1234", "domain": "acme.com", "php_version": "8.3"}
DEL = {"account": "abcd1234", "domain": "acme.com", "keep_archive": "yes"}
WEBSITE = ("CREATE_WEBSITE", "DELETE_WEBSITE", "SUSPEND_WEBSITE", "UNSUSPEND_WEBSITE", "CHANGE_PHP_VERSION", "SET_LIMITS")
SFTP = ("SFTP_LIST", "SFTP_CREATE", "SFTP_SET_PASSWORD", "SFTP_DELETE")
GOOD_PASSWORD = "Kq7vRt2mXzLp"


def step(command, params=None, **extra):
    return {"command": command, "params": params if params is not None else {}, **extra}


def scenarios():
    out = []
    lifecycle = [step("CREATE_WEBSITE", SITE), step("CREATE_WEBSITE", SITE), step("CREATE_WEBSITE", {**SITE, "domain": "other.com"}), step("SUSPEND_WEBSITE", {"account": "abcd1234"}),
                 step("SUSPEND_WEBSITE", {"account": "abcd1234"}), step("UNSUSPEND_WEBSITE", {"account": "abcd1234"}), step("UNSUSPEND_WEBSITE", {"account": "abcd1234"}),
                 step("CHANGE_PHP_VERSION", {**{k: SITE[k] for k in ("account", "domain")}, "php_version": "8.1"}), step("CHANGE_PHP_VERSION", {"account": "abcd1234", "domain": "acme.com", "php_version": "8.1"}),
                 step("CHANGE_PHP_VERSION", {"account": "nobody123", "domain": "x.com", "php_version": "8.1"}),
                 step("SET_LIMITS", {"account": "abcd1234", "disk_mb": 100, "memory_mb": 256, "processes": 20, "cpu_percent": 25, "inodes": 5000, "io_weight": 20}),
                 step("SET_LIMITS", {"account": "abcd1234", "disk_mb": 100, "memory_mb": 256, "processes": 20, "cpu_percent": 25, "inodes": 5000, "io_weight": 20}),
                 step("DELETE_WEBSITE", {**DEL, "domain": "other.com"}), step("DELETE_WEBSITE", DEL), step("DELETE_WEBSITE", DEL)]
    out.append({"name": "website lifecycle", "folders": [], "steps": lifecycle})
    out.append({"name": "website with every limit", "folders": [], "steps": [step("CREATE_WEBSITE", {**SITE, "disk_mb": 1000, "memory_mb": 512, "processes": 40, "cpu_percent": 50, "databases": 0,
                                                                                                    "sftp_accounts": 0, "inodes": 100000, "io_weight": 30}),
                                                                           step("SUSPEND_WEBSITE", {"account": "abcd1234"}), step("DELETE_WEBSITE", {**DEL, "keep_archive": "no"})]})
    for offset in range(0, 24):  # the create fails at the Nth command: what is undone, and in what order
        out.append({"name": f"create fails at command {offset}", "folders": [], "steps": [step("CREATE_WEBSITE", SITE, fail_offset=offset), step("CREATE_WEBSITE", SITE)]})
    for offset in range(0, 12):
        out.append({"name": f"delete fails at command {offset}", "folders": [], "steps": [step("CREATE_WEBSITE", SITE), step("DELETE_WEBSITE", DEL, fail_offset=offset)]})
    out.append({"name": "suspend and change fail", "folders": [], "steps": [step("CREATE_WEBSITE", SITE), step("SUSPEND_WEBSITE", {"account": "abcd1234"}, fail_offset=2),
                                                                         step("CHANGE_PHP_VERSION", {"account": "abcd1234", "domain": "acme.com", "php_version": "8.4"}, fail_offset=1),
                                                                         step("SET_LIMITS", {"account": "abcd1234", "disk_mb": 200, "memory_mb": 256, "processes": 20, "cpu_percent": 25}, fail_offset=3)]})
    out.append({"name": "invalid website commands", "folders": [], "steps": [step("CREATE_WEBSITE", {**SITE, "domain": "a.com\nServerName evil.com"}), step("CREATE_WEBSITE", {**SITE, "disk_mb": 5}),
                                                                           step("CREATE_WEBSITE", {"account": "abcd1234"}), step("SUSPEND_WEBSITE", {}), step("RUN_SHELL", {"cmd": "id"})]})

    sftp = [step("CREATE_WEBSITE", {**SITE, "sftp_accounts": 2}), step("SFTP_LIST", {"account": "abcd1234"}),
            step("SFTP_CREATE", {"account": "abcd1234", "name": "dev", "password": GOOD_PASSWORD, "directory": "acme.com/public_html"}),
            step("SFTP_CREATE", {"account": "abcd1234", "name": "dev", "password": GOOD_PASSWORD, "directory": ""}),
            step("SFTP_CREATE", {"account": "abcd1234", "name": "zed", "password": GOOD_PASSWORD, "directory": ""}),
            step("SFTP_CREATE", {"account": "abcd1234", "name": "third", "password": GOOD_PASSWORD, "directory": ""}),
            step("SFTP_CREATE", {"account": "abcd1234", "name": "bad", "password": GOOD_PASSWORD, "directory": "nope"}),
            step("SFTP_CREATE", {"account": "abcd1234", "name": "bad", "password": GOOD_PASSWORD, "directory": "../.."}),
            step("SFTP_SET_PASSWORD", {"account": "abcd1234", "name": "dev", "password": "Wp9sLn4cHdQe"}), step("SFTP_SET_PASSWORD", {"account": "abcd1234", "name": "ghost", "password": GOOD_PASSWORD}),
            step("SFTP_LIST", {"account": "abcd1234"}), step("SUSPEND_WEBSITE", {"account": "abcd1234"}), step("UNSUSPEND_WEBSITE", {"account": "abcd1234"}),
            step("SFTP_DELETE", {"account": "abcd1234", "name": "dev"}), step("SFTP_DELETE", {"account": "abcd1234", "name": "dev"}), step("SFTP_LIST", {"account": "abcd1234"}),
            step("DELETE_WEBSITE", DEL)]
    out.append({"name": "sftp logins", "folders": ["acme.com/public_html"], "steps": sftp})
    out.append({"name": "sftp create fails midway", "folders": [], "steps": [step("CREATE_WEBSITE", SITE),
                                                                            step("SFTP_CREATE", {"account": "abcd1234", "name": "dev", "password": GOOD_PASSWORD, "directory": ""}, fail_offset=5),
                                                                            step("SFTP_CREATE", {"account": "abcd1234", "name": "dev", "password": GOOD_PASSWORD, "directory": ""}, fail_offset=8)]})

    from .node.cron import job_id

    a = {"account": "abcd1234"}
    add = {**a, "schedule": "*/10 * * * *", "command": "php public_html/cron.php --quiet"}
    first = job_id("*/10 * * * *", "php public_html/cron.php --quiet")
    moved = job_id("0 2 * * *", "php public_html/cron.php --quiet")
    cron = [step("CRON_LIST", {"account": "nobody123"}), step("CREATE_WEBSITE", SITE), step("CRON_LIST", a), step("CRON_CREATE", add), step("CRON_CREATE", add),
            step("CRON_CREATE", {**add, "schedule": "*/4 * * * *"}), step("CRON_CREATE", {**add, "schedule": "61 * * * *"}), step("CRON_CREATE", {**add, "schedule": "0 25 * * *"}),
            step("CRON_CREATE", {**add, "command": "php ../x.php"}), step("CRON_CREATE", {**add, "command": "php a.php; id"}), step("CRON_CREATE", {**add, "command": "sh -c id"}),
            step("CRON_CREATE", {**add, "command": "php public_html/b.php a=1 b=2"}), step("CRON_UPDATE", {**a, "job_id": first, "schedule": "0 2 * * *"}),
            step("CRON_UPDATE", {**a, "job_id": first, "schedule": "0 2 * * *"}), step("CRON_UPDATE", {**a, "job_id": moved, "enabled": "no"}), step("CRON_LIST", a),
            step("CRON_UPDATE", {**a, "job_id": moved, "enabled": "yes"}), step("CRON_RUN", {**a, "job_id": moved}), step("CRON_RUN", {**a, "job_id": moved}, fail_offset=0),
            step("SUSPEND_WEBSITE", a), step("CRON_RUN", {**a, "job_id": moved}), step("UNSUSPEND_WEBSITE", a), step("CRON_DELETE", {**a, "job_id": moved}),
            step("CRON_DELETE", {**a, "job_id": moved}), step("CRON_LIST", a), step("DELETE_WEBSITE", {**DEL, "keep_archive": "no"})]
    out.append({"name": "cron jobs", "folders": [], "steps": cron})
    p = "abcd1234_"
    db = [step("CREATE_WEBSITE", {**SITE, "databases": 2}), step("DB_INVENTORY", {"account": "abcd1234"}), step("CREATE_DATABASE", {"account": "abcd1234", "name": p + "shop"}),
          step("CREATE_DATABASE", {"account": "abcd1234", "name": p + "shop"}), step("CREATE_DATABASE", {"account": "abcd1234", "name": p + "blog"}),
          step("CREATE_DATABASE", {"account": "abcd1234", "name": p + "third"}), step("CREATE_DATABASE", {"account": "abcd1234", "name": "other567_x"}),
          step("CREATE_DB_USER", {"account": "abcd1234", "name": p + "bob", "password": GOOD_PASSWORD}), step("CREATE_DB_USER", {"account": "abcd1234", "name": p + "bob", "password": GOOD_PASSWORD}),
          step("CREATE_DB_USER", {"account": "abcd1234", "name": "evil_bob", "password": GOOD_PASSWORD}), step("SET_DB_PASSWORD", {"account": "abcd1234", "name": p + "bob", "password": "Wp9sLn4cHdQe"}),
          step("SET_DB_PASSWORD", {"account": "abcd1234", "name": p + "nobody", "password": GOOD_PASSWORD}),
          step("GRANT_DB_ACCESS", {"account": "abcd1234", "database": p + "shop", "user": p + "bob", "privileges": ["select", "insert"]}),
          step("GRANT_DB_ACCESS", {"account": "abcd1234", "database": p + "blog", "user": p + "bob", "privileges": ["select"]}),
          step("GRANT_DB_ACCESS", {"account": "abcd1234", "database": "other567_x", "user": p + "bob", "privileges": ["select"]}),
          step("DB_INVENTORY", {"account": "abcd1234"}),
          {"seed_table": {"db": p + "shop", "table": "orders", "columns": [["id", "int", False, True, None], ["note", "text", True, False, "none"]],
                          "rows": [[1, "x" * 900], [2, "n2"], [3, None], [4, "n4"]]}},
          step("DB_TABLES", {"account": "abcd1234", "database": p + "shop"}), step("DB_DESCRIBE", {"account": "abcd1234", "database": p + "shop", "table": "orders"}),
          step("DB_DESCRIBE", {"account": "abcd1234", "database": p + "shop", "table": "nope"}),
          step("DB_BROWSE", {"account": "abcd1234", "database": p + "shop", "table": "orders", "offset": 0, "limit": 3}),
          step("DB_BROWSE", {"account": "abcd1234", "database": p + "shop", "table": "orders", "offset": 3, "limit": 3}), step("DB_BROWSE", {"account": "abcd1234", "database": "other567_x", "table": "t", "offset": 0, "limit": 3}),
          step("GRANT_DB_ACCESS", {"account": "abcd1234", "database": p + "shop", "user": p + "bob", "privileges": []}), step("DELETE_DB_USER", {"account": "abcd1234", "name": p + "bob"}),
          step("DELETE_DB_USER", {"account": "abcd1234", "name": p + "bob"}), step("DELETE_DATABASE", {"account": "abcd1234", "name": p + "blog"}), step("DELETE_DATABASE", {"account": "abcd1234", "name": p + "blog"}),
          step("DELETE_DATABASE", {"account": "abcd1234", "name": "other567_x"}), step("DB_INVENTORY", {"account": "abcd1234"}), step("DB_INVENTORY", {"account": "ghost999"})]
    out.append({"name": "databases", "folders": [], "steps": db})

    service = [step("SERVICE_RESTART", {"service": "httpd"}, service_state={"httpd": "active"}), step("SERVICE_RESTART", {"service": "mariadb"}, service_state={"mariadb": "inactive"}),
               step("SERVICE_RESTART", {"service": "httpd"}, fail_offset=0, service_state={"httpd": "active"}), step("SERVICE_RESTART", {"service": "sshd"}, fail_offset=1, service_state={"sshd": "active"}),
               step("SERVICE_RESTART", {"service": "postfix"}, service_state={"postfix": "active"}), step("SERVICE_RESTART", {"service": "dovecot"}, fail_offset=0, service_state={"dovecot": "active"}),
               step("SERVICE_RESTART", {"service": "firewalld"}), step("SERVICE_RESTART", {"service": "sshd; reboot"}), step("SERVICE_RESTART", {})]
    out.append({"name": "service restarts", "folders": [], "steps": service})
    return out


def _normalise(files):
    """Files that hold JSON are compared as data (key order and spacing are not what matters); empty values are the same as absent ones."""
    out = {}
    for path, text in sorted(files.items()):
        if path.endswith(".json"):
            out[path] = _strip(json.loads(text))
        else:
            out[path] = text
    return out


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if v not in (None, {}, [])}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def run(scenario):
    system, engine = FakeSystem(), FakeMariadb()
    folder = Path(tempfile.mkdtemp(prefix="whp-golden-"))
    try:
        for rel in scenario["folders"]:
            (folder / "abcd1234" / "sites" / rel).mkdir(parents=True)
        (folder / "abcd1234" / "sites").mkdir(parents=True, exist_ok=True)
        web = Websites(system)
        sftp = Sftp(system, lambda account: (folder / account / "sites") if (folder / account / "sites").is_dir() else None)
        databases = Databases(system, engine)
        cron_jobs = Cron(system, web, now=lambda: 1_800_000_000)
        service = ServiceControl(system, sleep=lambda seconds: None)
        results = []
        for item in scenario["steps"]:
            if "seed_table" in item:
                seed = item["seed_table"]
                engine.add_table(seed["db"], seed["table"], [tuple(c) for c in seed["columns"]], [list(r) for r in seed["rows"]])
                continue
            system.service_state.update(item.get("service_state", {}))
            system.fail_on = len(system.commands) + item["fail_offset"] if "fail_offset" in item else None
            command = item["command"]
            try:
                if command in WEBSITE:
                    code, data = web.run(command, item["params"])
                elif command in SFTP:
                    code, data = sftp.run(command, item["params"])
                elif command.startswith("CRON_"):
                    code, data = cron_jobs.run(command, item["params"])
                elif command == "SERVICE_RESTART":
                    code, data = service.run(command, item["params"])
                else:
                    code, data = databases.run(command, item["params"])
                results.append({"ok": True, "code": code, "data": json.loads(json.dumps(data))})
            except HandlerError as exc:
                results.append({"ok": False, "code": exc.code, "message": exc.message})
            except KeyError:  # a command none of the handlers know (the node answers "not implemented")
                results.append({"ok": False, "code": "not_implemented", "message": ""})
            system.fail_on = None
        return {"results": results, "files": _normalise(system.files), "dirs": sorted(system.dirs), "users": dict(sorted(system.users.items())), "commands": [list(c) for c in system.commands]}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def build_golden():
    return [{**{k: v for k, v in s.items()}, "expected": run(s)} for s in scenarios()]


# --- taking over a cPanel account (M2) ------------------------------------------------------------------------------------------------------

MIGRATE_ARCHIVE = "cpmove-acmeuser.tar.gz"


def migrate_steps(sha):
    a = {"account": "abcd1234", "archive": MIGRATE_ARCHIVE}
    return [step("CREATE_WEBSITE", {**SITE, "databases": 5}), step("MIGRATE_ANALYZE", a), step("MIGRATE_ANALYZE", {**a, "expected_sha256": "0" * 64}),
            step("MIGRATE_ANALYZE", {**a, "expected_sha256": sha.upper()}), step("MIGRATE_ANALYZE", {**a, "archive": "cpmove-nobody.tar.gz"}),
            step("MIGRATE_VALIDATE", a), step("MIGRATE_IMPORT", {**a, "part": "files"}), step("MIGRATE_IMPORT", {**a, "part": "files"}), step("MIGRATE_IMPORT", {**a, "part": "files", "replace": "yes"}),
            step("MIGRATE_IMPORT", {**a, "part": "databases"}), step("MIGRATE_IMPORT", {**a, "part": "databases"}),
            step("MIGRATE_IMPORT", {**a, "part": "cron"}), step("MIGRATE_IMPORT", {**a, "part": "cron"}), step("CRON_LIST", {"account": "abcd1234"}),
            step("MIGRATE_IMPORT", {**a, "part": "mail"}), step("MAIL_DOMAIN_ADD", {"account": "abcd1234", "domain": "acme.com"}, quiet=True),
            step("MIGRATE_IMPORT", {**a, "part": "mail"}), step("MIGRATE_IMPORT", {**a, "part": "mail"}), step("MIGRATE_VALIDATE", a),
            step("MIGRATE_IMPORT", {"account": "nobody123", "archive": MIGRATE_ARCHIVE, "part": "files"}),
            step("MIGRATE_DISCARD", {"archive": MIGRATE_ARCHIVE}), step("MIGRATE_DISCARD", {"archive": MIGRATE_ARCHIVE}), step("MIGRATE_ANALYZE", a)]


def run_migrate(archive_bytes):
    """The migration steps through the Python node on a pretend machine; everything observable is recorded, with the temporary folder replaced by <ROOT>."""
    import hashlib

    from .node.mail import Mail  # noqa: F401
    from .reference_agent import NativeMachine

    folder = Path(tempfile.mkdtemp(prefix="whp-migrate-"))
    root = str(folder).replace("\\", "/")

    def clean(text):
        return str(text).replace("\\", "/").replace(root, "<ROOT>")

    try:
        machine = NativeMachine(files_dir=folder, capabilities=("web", "database", "mail"))
        stage = folder / "_stage" / "migrations"
        stage.mkdir(parents=True)
        (stage / MIGRATE_ARCHIVE).write_bytes(archive_bytes)
        (folder / "abcd1234" / "sites").mkdir(parents=True)
        results = []
        for item in migrate_steps(hashlib.sha256(archive_bytes).hexdigest()):
            try:
                code, data = machine.run(item["command"], item["params"])
                results.append(None if item.get("quiet") else {"ok": True, "code": code, "data": json.loads(json.dumps(data))})
            except HandlerError as exc:
                results.append({"ok": False, "code": exc.code, "message": exc.message})
        tree = {}
        for base in (folder / "abcd1234", folder / "_mail"):
            for path in sorted(base.rglob("*")):
                if path.is_file() and path.suffix != ".json":
                    tree[clean(path)] = path.read_text(encoding="utf-8", errors="replace")
        mail = machine.mail._state("acme.com") or {}
        state = {"mailboxes": {k: v["hash"] for k, v in mail.get("mailboxes", {}).items()}, "forwarders": sorted(f"{f['source']}>{f['destination']}" for f in mail.get("forwarders", []))}
        databases = {db: {t: v["rows"] for t, v in tables.items()} for db, tables in machine.engine.dbs.items()}
        return {"results": results, "tree": tree, "mail": state, "databases": databases, "users": dict(sorted(machine.engine.users.items())),
                "grants": sorted([db, user, sorted(p)] for (db, user), p in machine.engine.grants.items()),
                "commands": [[clean(a) for a in c] for c in machine.system.commands if c and c[0] in ("chown", "chmod")]}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def build_migrate_golden():
    import tempfile as _t

    from apps.agent.tests.cpanel_archive import build

    with _t.TemporaryDirectory() as tmp:
        path = build(Path(tmp) / MIGRATE_ARCHIVE, user="acmeuser", domain="acme.com")
        data = path.read_bytes()
    return data, {"archive": MIGRATE_ARCHIVE, "steps": migrate_steps(__import__("hashlib").sha256(data).hexdigest()), "expected": run_migrate(data)}


# --- installing WordPress -------------------------------------------------------------------------------------------------------------------

WP_STEP = {"account": "abcd1234", "domain": "acme.com", "site_title": "Acme Shop", "admin_user": "siteadmin", "admin_email": "owner@gmail.example.com", "admin_password": "Kq7vRt2mXzLp-91"}


def wordpress_scenarios():
    install = step("INSTALL_WORDPRESS", WP_STEP)
    return [
        {"name": "installs", "fetch": {}, "files": {}, "steps": [install, install, step("INSTALL_WORDPRESS", {**WP_STEP, "account": "nobody123"})]},
        {"name": "tampered download", "fetch": {"tamper": True}, "files": {}, "steps": [install]},
        {"name": "network down", "fetch": {"down": True}, "files": {}, "steps": [install]},
        {"name": "folder not empty", "fetch": {}, "files": {"my-site.php": "<?php // mine"}, "steps": [install]},
        {"name": "hostile archive", "fetch": {"evil": True}, "files": {}, "steps": [install]},
        {"name": "no databases allowed", "fetch": {}, "files": {}, "limits": {"databases": 0}, "steps": [install]},
    ]


def run_wordpress(scenario):
    from . import wordpress_fixture as fixture
    from .reference_agent import NativeMachine

    folder = Path(tempfile.mkdtemp(prefix="whp-wp-"))
    root = str(folder).replace("\\", "/")

    def clean(text):
        return str(text).replace("\\", "/").replace(root, "<ROOT>")

    try:
        options = scenario["fetch"]
        archive = fixture.archive_bytes(evil=True) if options.get("evil") else None
        fetch = fixture.make_fetch(archive=archive, tamper=options.get("tamper", False), down=options.get("down", False))
        machine = NativeMachine(files_dir=folder, capabilities=("web", "database"), wordpress_fetch=fetch)
        machine.run("CREATE_WEBSITE", {**SITE, "databases": scenario.get("limits", {}).get("databases", 3)})
        docroot = folder / "abcd1234" / "sites" / "acme.com" / "public_html"
        docroot.mkdir(parents=True, exist_ok=True)
        (docroot / "index.html").write_bytes(b"placeholder")
        for rel, text in scenario["files"].items():
            (docroot / rel).write_bytes(text.encode())
        results = []
        for item in scenario["steps"]:
            try:
                code, data = machine.run(item["command"], item["params"])
                results.append({"ok": True, "code": code, "data": json.loads(json.dumps(data))})
            except HandlerError as exc:
                results.append({"ok": False, "code": exc.code, "message": exc.message})
        tree = {clean(p.relative_to(folder)): p.read_bytes().decode("utf-8", "replace") for p in sorted(docroot.rglob("*")) if p.is_file()}
        return {"results": results, "tree": tree, "databases": sorted(machine.engine.dbs), "users": sorted(machine.engine.users),
                "grants": sorted([db, user, sorted(p)] for (db, user), p in machine.engine.grants.items()),
                "commands": [[clean(a) for a in c] for c in machine.system.commands if c and c[0] in ("chown", "chmod", "systemd-run")]}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def build_wordpress_golden():
    from . import wordpress_fixture as fixture

    return {"archive": "wordpress.tar.gz", "evil": "wordpress-evil.tar.gz", "scenarios": [{**s, "expected": run_wordpress(s)} for s in wordpress_scenarios()]}, \
        fixture.archive_bytes(), fixture.archive_bytes(evil=True)
