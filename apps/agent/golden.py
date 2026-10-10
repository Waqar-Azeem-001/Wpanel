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
