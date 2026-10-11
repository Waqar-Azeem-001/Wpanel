"""
What a node does to take over a cPanel account (M2, ADR 19): read the account's own ``pkgacct`` backup (``cpmove-<user>.tar.gz``), say what is in it and what cannot
move by itself, then copy the files, databases and mailboxes into the account that WHPanel has already made on this node.

Safety rules that matter:
* **The archive is never trusted.** Every member is checked before it is read: no absolute path, no ``..``, links skipped, nothing outside the archive's own folder.
  Nothing from it is ever executed; SQL is only fed to the database client of THIS account's databases, and a database whose name does not start with ``<account>_`` is refused.
* **Nothing is overwritten silently.** A database that already exists on the node is refused (the import never replaces data); files are copied into the website's folder
  and an existing file is kept unless ``replace`` is set.
* **The import can be repeated**: running a part again gives the same result, so a lost answer is not a problem.
* The archive holds password hashes, so it is removed by MIGRATE_DISCARD as soon as the migration is finished or abandoned.

Only the reading and copying have run (on temporary folders against FakeMariadb and a pretend machine). A real ``pkgacct`` archive from a real cPanel server has NOT been
tried: the layout below follows the documented format and may differ between cPanel versions. See docs/m-cpanel-migration.md.
"""
import hashlib
import json
import os
import re
import shutil
import tarfile
from pathlib import Path

from .. import catalog
from .mariadb import ident
from .system import SystemError_
from .website import HandlerError

MIGRATION_DIR = "migrations"
SKIP_TOP = {".cpanel", ".cagefs", ".trash", ".cphorde", ".softaculous", ".cpan", ".spamassassin", ".razor", ".contactemail", ".lastlogin", "logs", "tmp", "access-logs",
            "mail", "etc", "ssl", "perl5", "lscache", ".bash_history", ".bash_logout", ".bash_profile", ".bashrc", ".cl.selector", ".htpasswds"}
MAX_MEMBERS = 2_000_000
ENV_LINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")
PHP_WORD = re.compile(r"^(?:/[A-Za-z0-9_./-]*/)?php[0-9.]*$")
SHELL_SYMBOLS = re.compile(r"[;|&<>$`()\\]")
REDIRECT_TAIL = re.compile(r"\s*(?:>>?\s*/dev/null)?(?:\s*2>&1)?\s*$")
CRON_MACROS = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *"}


def cron_jobs_of(text):
    """The job lines of a crontab: comments, blank lines and NAME=value settings are not jobs."""
    return [l for l in (text or "").splitlines() if l.strip() and not l.lstrip().startswith("#") and not ENV_LINE.match(l.strip())]


def convert_cron_line(line, user):
    """A cPanel crontab line as (schedule, "php script args"), or ValueError with the reason it cannot become a native job (only php scripts inside the account can)."""
    line = line.strip()
    if line.startswith("@"):
        macro, _, command = line.partition(" ")
        if macro not in CRON_MACROS:
            raise ValueError(f"{macro} is not supported")
        schedule = CRON_MACROS[macro]
    else:
        parts = line.split(None, 5)
        if len(parts) < 6:
            raise ValueError("not a schedule and a command")
        schedule, command = " ".join(parts[:5]), parts[5]
    command = REDIRECT_TAIL.sub("", command.strip())
    if SHELL_SYMBOLS.search(command):
        raise ValueError("uses shell features (pipes, redirects, variables)")
    tokens = command.split()
    if not tokens or not PHP_WORD.match(tokens[0]):
        raise ValueError("is not a php script")
    if len(tokens) < 2 or tokens[1].startswith("-"):
        raise ValueError("php options are not supported")
    script = tokens[1]
    if script.startswith(f"/home/{user}/"):
        script = script[len(f"/home/{user}/"):]
    elif script.startswith("/"):
        raise ValueError("runs a script outside the account")
    script = script[2:] if script.startswith("./") else script
    return schedule, " ".join(["php", script, *tokens[2:]])
PHP_MAP = {"ea-php74": "8.1", "ea-php80": "8.1", "ea-php81": "8.1", "ea-php82": "8.2", "ea-php83": "8.3", "ea-php84": "8.4"}  # an older PHP moves up to the oldest one this platform offers


# --- reading the archive ----------------------------------------------------------------------------------------------------------------------

def _clean(name):
    """The member's path inside the archive's top folder, or None if it must be ignored. Raises for anything dangerous."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if ".." in parts or name.startswith("/") or any(":" in p for p in parts[:1]):
        raise HandlerError("unsafe_archive", "The archive holds a path that leads outside itself.")
    return "/".join(parts[1:]) if len(parts) > 1 else ""


class Archive:
    """A pkgacct archive opened for reading. ``index`` lists regular files and folders (never links); ``read`` returns one small file's bytes."""

    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise HandlerError("not_found", "That archive has not arrived on this node.")
        self.files, self.dirs, self.skipped_links = {}, set(), 0
        try:
            with tarfile.open(self.path, "r:*") as tar:
                for count, member in enumerate(tar):
                    if count > MAX_MEMBERS:
                        raise HandlerError("unsafe_archive", "The archive holds an unreasonable number of files.")
                    rel = _clean(member.name)
                    if member.issym() or member.islnk():
                        self.skipped_links += 1
                    elif member.isdir():
                        if rel:
                            self.dirs.add(rel)
                    elif member.isfile() and rel:
                        self.files[rel] = member.size
                    elif not member.isfile():
                        self.skipped_links += 1  # devices, pipes: never copied
        except (tarfile.TarError, EOFError, OSError) as exc:
            raise HandlerError("bad_archive", f"The archive cannot be read: {str(exc)[:120]}") from None

    def sha256(self):
        digest = hashlib.sha256()
        with open(self.path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def read(self, rel, limit=8 << 20):
        with tarfile.open(self.path, "r:*") as tar:
            for member in tar:
                if member.isfile() and _clean(member.name) == rel:
                    handle = tar.extractfile(member)
                    return handle.read(limit) if handle else b""
        return None

    def text(self, rel, limit=8 << 20):
        raw = self.read(rel, limit)
        return None if raw is None else raw.decode("utf-8", "replace")

    def extract(self, prefix, target, replace=False, skip=lambda rel: False):
        """Copy every regular file under ``prefix`` to ``target``. Returns (copied, kept_existing). Links and anything unsafe are skipped."""
        target = Path(target)
        copied = kept = 0
        with tarfile.open(self.path, "r:*") as tar:
            for member in tar:
                rel = _clean(member.name)
                if not (member.isfile() or member.isdir()) or not (rel == prefix or rel.startswith(prefix + "/")):
                    continue
                inner = rel[len(prefix):].lstrip("/")
                if skip(inner):
                    continue
                destination = target / inner
                if not str(destination.resolve()).startswith(str(target.resolve())):
                    continue
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                if destination.exists() and not replace:
                    kept += 1
                    continue
                if any(p.is_symlink() for p in [destination, *destination.parents] if str(p).startswith(str(target))):
                    continue  # never write through a link that is already on the node
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                with open(destination, "wb") as out:
                    shutil.copyfileobj(source, out)
                copied += 1
        return copied, kept


def parse_kv(text):
    """cPanel's ``cp/<user>`` file: one ``KEY=value`` per line."""
    out = {}
    for line in (text or "").splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            out.setdefault(key.strip(), value.strip())
    return out


def parse_yaml_flat(text):
    """The few top-level facts needed from cPanel's userdata files (``key: value`` and ``key:`` followed by ``- item`` lines). Not a general YAML reader."""
    out, current = {}, None
    for line in (text or "").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("- ") or line.startswith("  - "):
            if current is not None:
                out.setdefault(current, []).append(line.split("- ", 1)[1].strip().strip("'\""))
            continue
        if line[0] != " " and ":" in line:
            key, _, value = line.partition(":")
            current = key.strip()
            value = value.strip().strip("'\"")
            if value:
                out[current] = value
            else:
                out[current] = []
    return out


GRANT = re.compile(r"GRANT\s+(?P<privs>.+?)\s+ON\s+`?(?P<db>[^`.]+?)`?\.\*\s+TO\s+'(?P<user>[^']+)'@'(?P<host>[^']*)'", re.I)
IDENT_HASH = re.compile(r"IDENTIFIED BY PASSWORD '(\*[0-9A-Fa-f]{40})'", re.I)
GRANT_USAGE = re.compile(r"GRANT\s+USAGE\s+ON\s+\*\.\*\s+TO\s+'(?P<user>[^']+)'@'[^']*'\s+IDENTIFIED BY PASSWORD '(?P<hash>\*[0-9A-Fa-f]{40})'", re.I)
MAP_PRIV = {"SELECT": "select", "INSERT": "insert", "UPDATE": "update", "DELETE": "delete", "CREATE": "create", "ALTER": "alter", "DROP": "drop", "INDEX": "index"}


def parse_mysql_grants(text):
    """From cPanel's ``mysql.sql``: {user: hash} and a list of (database, user, [privileges the platform knows]). Privileges it has no equivalent for are returned apart."""
    hashes, grants, unsupported = {}, [], set()
    for line in (text or "").splitlines():
        usage = GRANT_USAGE.search(line)
        if usage:
            hashes[usage.group("user")] = usage.group("hash").upper()
            continue
        grant = GRANT.search(line)
        if not grant:
            continue
        raw = [p.strip().upper() for p in grant.group("privs").split(",")]
        db = grant.group("db").replace("\\_", "_")
        if raw == ["ALL PRIVILEGES"] or raw == ["ALL"]:
            privs = sorted(MAP_PRIV.values())
        else:
            privs = sorted({MAP_PRIV[p] for p in raw if p in MAP_PRIV})
            unsupported |= {p for p in raw if p not in MAP_PRIV}
        grants.append((db, grant.group("user"), privs))
    return hashes, grants, sorted(unsupported)


def parse_shadow(text):
    """``etc/<domain>/shadow``: ``local:hash:...`` per mailbox. Returns {local: hash}."""
    out = {}
    for line in (text or "").splitlines():
        bits = line.split(":")
        if len(bits) >= 2 and bits[0] and bits[1] and not bits[1].startswith(("!", "*")):
            out[bits[0]] = bits[1]
    return out


def parse_valiases(text):
    """``va/<domain>``: ``source: destination`` lines (a destination can be several, comma separated). Returns [(source, destination)]."""
    out = []
    for line in (text or "").splitlines():
        if ":" in line and not line.startswith("#"):
            source, _, destination = line.partition(":")
            for dest in destination.split(","):
                dest = dest.strip()
                if source.strip() and "@" in dest and "|" not in dest and "/" not in dest:
                    out.append((source.strip(), dest))
    return out


# --- discovery and pre-flight ------------------------------------------------------------------------------------------------------------------

def discover(archive, account=None):
    """Everything the archive holds, as plain data, with the problems found: ``blockers`` stop an automatic migration, ``warnings`` do not."""
    names = list(archive.files)
    user = next((n.split("/", 1)[1] for n in names if n.startswith("cp/")), None)
    info = parse_kv(archive.text(f"cp/{user}")) if user else {}
    main = parse_yaml_flat(archive.text("userdata/main"))
    main_domain = main.get("main_domain") or info.get("DNS", "")
    addon = sorted(main.get("addon_domains", {}) if isinstance(main.get("addon_domains"), dict) else (main.get("addon_domains") or []))
    subs = [s for s in (main.get("sub_domains") or []) if s]
    parked = [d for d in (main.get("parked_domains") or []) if d]
    site = parse_yaml_flat(archive.text(f"userdata/{main_domain}")) if main_domain else {}
    php_raw = site.get("phpversion", "")
    databases = sorted({n.split("/", 1)[1][:-4] for n in names if n.startswith("mysql/") and n.endswith(".sql")})
    sizes = {d: archive.files.get(f"mysql/{d}.sql", 0) for d in databases}
    hashes, grants, unsupported = parse_mysql_grants(archive.text("mysql.sql"))
    mail = {}
    for n in names:
        parts = n.split("/")
        if len(parts) >= 4 and parts[0] == "homedir" and parts[1] == "mail" and parts[2] not in ("cur", "new", "tmp") and not parts[2].startswith("."):
            mail.setdefault(parts[2], {}).setdefault(parts[3], 0)
            mail[parts[2]][parts[3]] += archive.files[n]
    cron = cron_jobs_of(archive.text(f"cron/{user}")) if user else []
    zones = sorted(n.split("/", 1)[1][:-3] for n in names if n.startswith("dnszones/") and n.endswith(".db"))
    certs = sorted(n for n in names if n.startswith("sslcerts/") or n.startswith("ssl/"))
    home_files = [n for n in names if n.startswith("homedir/") and not _skipped(n[len("homedir/"):])]
    report = {
        "user": user or "", "main_domain": main_domain, "addon_domains": addon, "sub_domains": subs, "parked_domains": parked,
        "php_cpanel": php_raw, "php_target": PHP_MAP.get(php_raw, "8.2" if php_raw else "8.2"),
        "plan": info.get("PLAN", ""), "contact_email": info.get("CONTACTEMAIL", ""),
        "files": len(home_files), "files_mb": round(sum(archive.files[n] for n in home_files) / 1048576, 2),
        "databases": [{"name": d, "bytes": sizes[d]} for d in databases], "db_users": sorted(hashes), "db_grants": len(grants),
        "mail": [{"domain": d, "mailboxes": sorted(boxes), "bytes": sum(boxes.values())} for d, boxes in sorted(mail.items())],
        "forwarders": sum(len(parse_valiases(archive.text(f"va/{d}"))) for d in mail),
        "cron": cron, "dns_zones": zones, "certificates": len(certs), "skipped_links": archive.skipped_links,
        "archive_sha256": archive.sha256(), "archive_mb": round(archive.path.stat().st_size / 1048576, 2),
    }
    blockers, warnings = [], []
    if not user or not main_domain:
        blockers.append("This does not look like a cPanel account backup (no account or main domain found).")
    if account and user and user != account:
        warnings.append(f"The cPanel account is called {user}; on this platform it is {account}. Database names starting with {user}_ are renamed to {account}_.")
    if not home_files and not any(n.startswith("homedir/") for n in names):
        blockers.append("The backup holds no website files (homedir is missing).")
    if addon or subs or parked:
        blockers.append("The account has " + ", ".join(x for x in (f"{len(addon)} addon" if addon else "", f"{len(subs)} sub" if subs else "", f"{len(parked)} parked" if parked else "") if x)
                        + " domain(s). A native account serves one domain, so these need a manual decision (give each its own account, or leave them out).")
    if cron:
        warnings.append(f"{len(cron)} scheduled job(s) (cron): plain php scripts inside the account are recreated, anything else is listed so it can be recreated by hand.")
    if unsupported:
        warnings.append("Some database privileges have no equivalent here and were left out: " + ", ".join(unsupported) + ".")
    if report["certificates"]:
        warnings.append("Certificates are not moved: a new one is issued for the site after the cutover.")
    if report["mail"]:
        warnings.append("Mail is imported on the mail node; mailbox passwords keep working only if the archive holds their hashes.")
    if php_raw and php_raw not in PHP_MAP:
        warnings.append(f"PHP {php_raw} is unknown; {report['php_target']} will be used.")
    elif php_raw and php_raw in ("ea-php74", "ea-php80"):
        warnings.append(f"The site ran {php_raw}, which is no longer offered; it will run on PHP {report['php_target']}. Test it before the cutover.")
    if archive.skipped_links:
        warnings.append(f"{archive.skipped_links} link(s) in the archive were ignored for safety.")
    report["blockers"], report["warnings"] = blockers, warnings
    return report


def _skipped(rel):
    top = rel.split("/", 1)[0]
    return top in SKIP_TOP or top == "" or "/" not in rel  # loose files in the cPanel home folder (shell dotfiles and the like) are not website files


# --- the node -----------------------------------------------------------------------------------------------------------------------------

class Migrator:
    def __init__(self, system, stage_root, root_for, engine, websites=None, mail=None, cron=None):
        self.s, self.stage_root, self.root_for, self.engine, self.web, self.mail, self.cron = system, Path(stage_root), root_for, engine, websites, mail, cron

    def _archive(self, name):
        return Archive(self.stage_root / MIGRATION_DIR / name)

    def run(self, command, params):
        try:
            p = catalog.validate(command, params)
            handler = {"MIGRATE_ANALYZE": self.analyze, "MIGRATE_IMPORT": self.import_part, "MIGRATE_VALIDATE": self.validate, "MIGRATE_DISCARD": self.discard}[command]
            return handler(p)
        except catalog.InvalidCommand as exc:
            raise HandlerError("invalid", str(exc)) from None
        except SystemError_ as exc:
            raise HandlerError("system_error", str(exc)[:300]) from None

    def analyze(self, p):
        archive = self._archive(p["archive"])
        report = discover(archive, p["account"])
        if p.get("expected_sha256") and p["expected_sha256"].lower() != report["archive_sha256"]:
            raise HandlerError("checksum_mismatch", "The archive that arrived is not the one that was made (its checksum differs): copy it again.")
        report["mail"] = [{**m, "mailboxes": m["mailboxes"][:200]} for m in report["mail"]]
        report["cron"] = report["cron"][:50]
        return "analyzed", report

    # --- import --------------------------------------------------------------------------------------------------------------------------
    def import_part(self, p):
        account, archive = p["account"], self._archive(p["archive"])
        report = discover(archive, account)
        part = p["part"]
        if part == "files":
            return self._files(account, archive, report, p.get("replace") == "yes")
        if part == "databases":
            return self._databases(account, archive, report)
        if part == "cron":
            return self._cron(account, archive, report)
        return self._mail(account, archive, report)

    def _cron(self, account, archive, report):
        if self.cron is None:
            raise HandlerError("not_applicable", "This node does not run scheduled jobs.")
        created = exists = 0
        skipped = []
        for line in cron_jobs_of(archive.text(f"cron/{report['user']}")):
            try:
                schedule, command = convert_cron_line(line, report["user"])
                code, _ = self.cron.create({"account": account, "schedule": schedule, "command": command})
                created, exists = created + (code == "created"), exists + (code == "exists")
            except ValueError as exc:
                skipped.append({"line": line[:120], "reason": str(exc)})
            except HandlerError as exc:  # the node's own rules (every five minutes at most, ten jobs) have the last word
                skipped.append({"line": line[:120], "reason": exc.message[:120]})
        return "imported", {"created": created, "already_there": exists, "skipped": skipped[:30]}

    def _files(self, account, archive, report, replace):
        state = self.web._state(account) if self.web else None
        if state is None:
            raise HandlerError("not_found", "Make the website on this node first.")
        site = Path(self.root_for(account)) / state["domain"]
        docroot = site / "public_html"
        copied, kept = archive.extract("homedir/public_html", docroot, replace)
        extra = 0
        tops = sorted({n.split("/")[1] for n in archive.files if n.startswith("homedir/") and n.count("/") >= 2} - {"public_html"})
        for top in tops:
            if _skipped(top + "/x"):
                continue
            c, k = archive.extract(f"homedir/{top}", site / top, replace)
            extra, kept = extra + c, kept + k
        # the agent runs as root, so what it wrote is root's: hand it to the account (group apache, as for every other file of the site) and keep other accounts out
        self.s.run(["chown", "-R", f"{account}:apache", str(site)])
        self.s.run(["chmod", "-R", "u+rwX,g+rX,o-rwx", str(site)])
        return "imported", {"files": copied + extra, "kept_existing": kept, "outside_public_html": extra}

    def _renamed(self, name, source, account):
        return account + name[len(source):] if source != account and name.startswith(source + "_") else name

    def _databases(self, account, archive, report):
        source = report["user"] or account
        hashes, grants, _ = parse_mysql_grants(archive.text("mysql.sql"))
        existing = set(self.engine.databases(account + "_"))
        done, skipped = [], []
        for entry in report["databases"]:
            name = ident(self._renamed(entry["name"], source, account))
            if not name.startswith(account + "_"):
                raise HandlerError("forbidden", f"The archive holds a database ({entry['name']}) that does not belong to this account.")
            if name in existing:
                skipped.append(name)  # imported before (or made by hand): never replaced
                continue
            self.engine.create_db(name)
            try:
                self.engine.load_sql(name, archive, f"mysql/{entry['name']}.sql")
            except Exception:
                self.engine.drop_db(name)  # a half-loaded database is worse than none
                raise
            done.append(name)
        users = set(self.engine.users_with(account + "_"))
        made = []
        for user, hash_ in sorted(hashes.items()):
            name = ident(self._renamed(user, source, account))
            if not name.startswith(account + "_") or name in users:
                continue
            self.engine.create_user(name, hash_, max_connections=20)
            made.append(name)
        for db, user, privs in grants:
            db_name, user_name = self._renamed(db, source, account), self._renamed(user, source, account)
            if db_name in self.engine.databases(account + "_") and user_name in self.engine.users_with(account + "_") and privs:
                self.engine.set_grants(db_name, user_name, privs)
        return "imported", {"databases": done, "already_there": skipped, "users": made}

    def _mail(self, account, archive, report):
        if self.mail is None:
            raise HandlerError("not_applicable", "This node does not hold mail.")
        counts = {"mailboxes": 0, "messages": 0, "forwarders": 0}
        skipped = []
        for entry in report["mail"]:
            domain = entry["domain"]
            state = self.mail._state(domain)
            if state is None or state["account"] != account:
                skipped.append(domain)  # the domain must be set up for this account on this node first
                continue
            hashes = parse_shadow(archive.text(f"homedir/etc/{domain}/shadow"))
            everything = self.mail._all()
            for local in entry["mailboxes"]:
                if local not in state["mailboxes"]:
                    if not (hashes.get(local) or "").startswith("$"):
                        skipped.append(f"{local}@{domain}")  # no usable hash: the customer sets a new password
                        continue
                    state["mailboxes"][local] = {"quota_mb": 0, "hash": hashes[local], "blocked": False, "sent": []}
                    counts["mailboxes"] += 1
                target = self.mail.mail_root / domain / local
                for folder in ("", ".Drafts", ".Sent", ".Archive", ".Junk", ".Trash"):
                    for sub in ("cur", "new", "tmp"):
                        (target / folder / sub).mkdir(parents=True, exist_ok=True)
                counts["messages"] += archive.extract(f"homedir/mail/{domain}/{local}", target, False, skip=lambda rel: rel.endswith((".index", ".log", ".cache")))[0]
            for source, destination in parse_valiases(archive.text(f"va/{domain}")):
                if source.endswith("@" + domain) and destination not in [f["destination"] for f in state["forwarders"] if f["source"] == source]:
                    state["forwarders"].append({"source": source, "destination": destination})
                    counts["forwarders"] += 1
            self.mail._commit(state, everything)
            self.s.run(["chown", "-R", "5000:5000", str(self.mail.mail_root / domain)])  # the mail user (VMAIL_UID) owns every mailbox
        return "imported", {**counts, "skipped": skipped}

    # --- validate / discard -----------------------------------------------------------------------------------------------------------------
    def validate(self, p):
        """Does what arrived match what the archive held? Counts files, and checks each database exists with its tables."""
        account, archive = p["account"], self._archive(p["archive"])
        report = discover(archive, account)
        state = self.web._state(account) if self.web else None
        if state is None:
            raise HandlerError("not_found", "No such website on this node.")
        site = Path(self.root_for(account)) / state["domain"]
        expected = {n[len("homedir/"):] for n in archive.files if n.startswith("homedir/") and not _skipped(n[len("homedir/"):])}  # exactly the files the import copies
        missing = sorted(e for e in expected if not (site / e).is_file())
        source = report["user"] or account
        databases = []
        for entry in report["databases"]:
            name = self._renamed(entry["name"], source, account)
            there = name in self.engine.databases(account + "_")
            databases.append({"name": name, "present": there, "tables": len(self.engine.tables(name)) if there else 0})
        entry = site / "public_html"
        index = next((n for n in ("index.php", "index.html") if (entry / n).is_file()), "")
        ok = not missing and all(d["present"] for d in databases)
        return "validated" if ok else "differences", {"ok": ok, "files_expected": len(expected), "files_missing": len(missing), "missing_sample": missing[:10], "databases": databases,
                                                    "index": index, "php": (state or {}).get("php_version", "")}

    def discard(self, p):
        path = self.stage_root / MIGRATION_DIR / p["archive"]
        if not path.is_file():
            return "absent", {}
        path.unlink()
        return "discarded", {}
