"""
The advanced website tools that need nothing special from the server except a web server that reads ``.htaccess`` (Apache and LiteSpeed, true on cPanel): password-protected
folders, hotlink protection, folder listing on or off, and blocking addresses.

Every rule lives in a marked block (``# BEGIN WHPanel <name>`` ... ``# END WHPanel <name>``) so it can be switched off again cleanly and nothing else in the file is touched.
What is switched on is also kept in a small registry file (``.whp-tools.json`` in the home folder) so the page shows the real choices after a reload. Everything goes through the
files capability, as the customer (checked and audited). Passwords are written only as a salted hash, in a folder above the website.
"""
import hashlib
import ipaddress
import json
import re

from apps.core.exceptions import ServiceError

from . import care, htpasswd, site_apps
from .adapters.capabilities import Capability

REGISTRY = ".whp-tools.json"
AUTH_DIR = ".whp-auth"
WEB_ROOT = "public_html"
HOTLINK_TYPES = ("jpg", "jpeg", "png", "gif", "webp", "svg", "ico", "mp4", "mp3", "pdf", "zip")
MAX_IPS = 100
MAX_USERS_PER_FOLDER = 20


# --- marked blocks ------------------------------------------------------------------------------------------------------

def _markers(name):
    return f"# BEGIN WHPanel {name}", f"# END WHPanel {name}"


def remove_block(text, name):
    begin, end = _markers(name)
    cleaned = re.sub(r"\n?" + re.escape(begin) + r".*?" + re.escape(end) + r"\n?", "\n", text or "", flags=re.S).strip("\n")
    return cleaned + ("\n" if cleaned else "")


def set_block(text, name, body):
    """``text`` with the block present (``body`` given) or absent (``body`` empty), every other line untouched. A new block goes first, so it applies before WordPress's own rules."""
    cleaned = remove_block(text, name)
    if not body:
        return cleaned
    begin, end = _markers(name)
    return f"{begin}\n{body.strip(chr(10))}\n{end}\n" + ("\n" + cleaned if cleaned else "")


def has_block(text, name):
    return _markers(name)[0] in (text or "")


# --- files, as the customer ---------------------------------------------------------------------------------------------

def _read(io, path, limit=400_000):
    try:
        raw = io.call(Capability.FILES, "read_file", path, max_bytes=limit)
    except ServiceError:
        return ""
    return raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)


def _write(io, path, text, purpose):
    io.call(Capability.FILES, "write_file", path, text.encode("utf-8"), overwrite=True, audit_metadata={"path": path, "purpose": purpose})


def _ensure_dir(io, parent, name):
    if name not in {e.name for e in io.call(Capability.FILES, "list_files", parent)}:
        io.call(Capability.FILES, "make_directory", f"{parent}/{name}" if parent else name)


def _io(user, account):
    return care._Io(user, account)


def _clean_folder(path):
    """A folder inside the website's own area, as a path relative to the home folder."""
    path = (path or "").strip().strip("/")
    parts = [p for p in path.split("/") if p]
    if not parts or parts[0] != WEB_ROOT or any(p in (".", "..") or p.startswith(".") for p in parts):
        raise ServiceError("Choose a folder inside your website's folder (public_html).", code="bad_folder")
    if any(not re.fullmatch(r"[A-Za-z0-9._ -]{1,100}", p) for p in parts):
        raise ServiceError("That folder name has characters that are not allowed.", code="bad_folder")
    return "/".join(parts)


def _slug(path):
    return hashlib.sha1(path.encode()).hexdigest()[:12]


# --- the registry -------------------------------------------------------------------------------------------------------

def state(user, account):
    """What is switched on: {"protect": [{path, users}], "indexes": [{path, listing}], "hotlink": {...}, "ips": [{ip, note}]}."""
    io = _io(user, account)
    try:
        data = json.loads(_read(io, REGISTRY) or "{}")
    except ValueError:
        data = {}
    data.setdefault("protect", [])
    data.setdefault("indexes", [])
    data.setdefault("hotlink", {})
    data.setdefault("ips", [])
    return data


def _save_state(io, data):
    _write(io, REGISTRY, json.dumps(data, indent=1), "advanced tools registry")


def _htaccess(path):
    return f"{path}/.htaccess" if path else ".htaccess"


# --- password-protected folders -----------------------------------------------------------------------------------------

def protect_folder(user, account, path, login, password):
    path = _clean_folder(path)
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,32}", login or ""):
        raise ServiceError("The username needs 3 to 32 letters, numbers, dots, dashes or underscores.", code="bad_login")
    if len(password or "") < 8:
        raise ServiceError("Use a password of at least 8 characters.", code="weak_password")
    io = _io(user, account)
    data = state(user, account)
    entry = next((e for e in data["protect"] if e["path"] == path), None)
    users = list(entry["users"]) if entry else []
    if login not in users and len(users) >= MAX_USERS_PER_FOLDER:
        raise ServiceError("A folder can have up to 20 users.", code="limit_reached")
    home = io.call(Capability.FILES, "home_path")
    _ensure_dir(io, "", AUTH_DIR)
    password_file = f"{AUTH_DIR}/{_slug(path)}.htpasswd"
    existing = {l.split(":", 1)[0]: l for l in _read(io, password_file).splitlines() if ":" in l}
    existing[login] = htpasswd.line(login, password)
    _write(io, password_file, "\n".join(existing.values()) + "\n", "password-protected folder users")
    block = ("AuthType Basic\nAuthName \"Restricted area\"\n"
             f"AuthUserFile {home}/{password_file}\nRequire valid-user")
    target = _htaccess(path)
    _write(io, target, set_block(_read(io, target), "password", block), "password-protect a folder")
    if entry is None:
        entry = {"path": path, "users": []}
        data["protect"].append(entry)
    if login not in entry["users"]:
        entry["users"].append(login)
    _save_state(io, data)
    return entry


def unprotect_folder(user, account, path):
    path = _clean_folder(path)
    io = _io(user, account)
    data = state(user, account)
    target = _htaccess(path)
    _write(io, target, set_block(_read(io, target), "password", ""), "remove folder password protection")
    try:
        io.call(Capability.FILES, "delete_path", f"{AUTH_DIR}/{_slug(path)}.htpasswd")
    except ServiceError:
        pass
    data["protect"] = [e for e in data["protect"] if e["path"] != path]
    _save_state(io, data)


def remove_protect_user(user, account, path, login):
    path = _clean_folder(path)
    data = state(user, account)
    entry = next((e for e in data["protect"] if e["path"] == path), None)
    if entry is None or login not in entry["users"]:
        raise ServiceError("That user does not exist.", code="no_user")
    if len(entry["users"]) == 1:
        return unprotect_folder(user, account, path)
    io = _io(user, account)
    password_file = f"{AUTH_DIR}/{_slug(path)}.htpasswd"
    kept = [l for l in _read(io, password_file).splitlines() if l and l.split(":", 1)[0] != login]
    _write(io, password_file, "\n".join(kept) + "\n", "password-protected folder users")
    entry["users"].remove(login)
    _save_state(io, data)


# --- hotlink protection -------------------------------------------------------------------------------------------------

def hotlink_block(domains, types):
    names = "|".join(re.escape(d) for d in domains)
    return ("<IfModule mod_rewrite.c>\nRewriteEngine On\nRewriteCond %{HTTP_REFERER} !^$\n"
            f"RewriteCond %{{HTTP_REFERER}} !^https?://([a-z0-9-]+\\.)*({names})(:[0-9]+)?(/.*)?$ [NC]\n"
            f"RewriteRule \\.({'|'.join(types)})$ - [F,NC,L]\n</IfModule>")


def set_hotlink(user, account, enabled, domains=(), types=()):
    io = _io(user, account)
    data = state(user, account)
    target = _htaccess(WEB_ROOT)
    if not enabled:
        _write(io, target, set_block(_read(io, target), "hotlink", ""), "hotlink protection off")
        data["hotlink"] = {}
    else:
        names = [d.strip().lower().rstrip(".") for d in domains if d and d.strip()]
        names = [account.domain.lower()] + [n for n in names if n != account.domain.lower()]
        if any(not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", n) for n in names):
            raise ServiceError("One of the allowed domains is not a valid domain name.", code="bad_domain")
        kinds = [t for t in types if t in HOTLINK_TYPES] or ["jpg", "jpeg", "png", "gif", "webp"]
        _write(io, target, set_block(_read(io, target), "hotlink", hotlink_block(names, kinds)), "hotlink protection on")
        data["hotlink"] = {"enabled": True, "domains": names, "types": kinds}
    _save_state(io, data)
    return data["hotlink"]


# --- folder listing -----------------------------------------------------------------------------------------------------

def set_listing(user, account, path, listing):
    """``listing`` False hides the file list of a folder with no index page; True shows it."""
    path = _clean_folder(path)
    io = _io(user, account)
    data = state(user, account)
    target = _htaccess(path)
    body = "Options +Indexes" if listing else "Options -Indexes"
    _write(io, target, set_block(_read(io, target), "listing", body), "folder listing")
    data["indexes"] = [e for e in data["indexes"] if e["path"] != path] + [{"path": path, "listing": bool(listing)}]
    _save_state(io, data)


def reset_listing(user, account, path):
    path = _clean_folder(path)
    io = _io(user, account)
    data = state(user, account)
    target = _htaccess(path)
    _write(io, target, set_block(_read(io, target), "listing", ""), "folder listing reset")
    data["indexes"] = [e for e in data["indexes"] if e["path"] != path]
    _save_state(io, data)


# --- blocked addresses --------------------------------------------------------------------------------------------------

def ip_block(addresses):
    lines = "\n".join(f"Require not ip {a}" for a in addresses)
    return f"<IfModule mod_authz_core.c>\n<RequireAll>\nRequire all granted\n{lines}\n</RequireAll>\n</IfModule>"


def _clean_network(text):
    try:
        return str(ipaddress.ip_network((text or "").strip(), strict=False))
    except ValueError:
        raise ServiceError("Enter an IP address (1.2.3.4) or a range (1.2.3.0/24).", code="bad_ip") from None


def _apply_ips(io, data):
    target = _htaccess(WEB_ROOT)
    body = ip_block([i["ip"] for i in data["ips"]]) if data["ips"] else ""
    _write(io, target, set_block(_read(io, target), "ipblock", body), "blocked addresses")


def block_ip(user, account, ip, note="", your_ip=""):
    network = _clean_network(ip)
    if your_ip:
        try:
            if ipaddress.ip_address(your_ip) in ipaddress.ip_network(network):
                raise ServiceError("That would block the address you are using right now.", code="blocks_you")
        except ValueError:
            pass
    io = _io(user, account)
    data = state(user, account)
    if any(i["ip"] == network for i in data["ips"]):
        raise ServiceError("That address is already blocked.", code="duplicate")
    if len(data["ips"]) >= MAX_IPS:
        raise ServiceError("You can block up to 100 addresses.", code="limit_reached")
    data["ips"].append({"ip": network, "note": (note or "")[:100]})
    _apply_ips(io, data)
    _save_state(io, data)


def unblock_ip(user, account, ip):
    try:
        ip = _clean_network(ip)  # "1.2.3.4" and "1.2.3.4/32" are the same address
    except ServiceError:
        pass
    io = _io(user, account)
    data = state(user, account)
    data["ips"] = [i for i in data["ips"] if i["ip"] != ip]
    _apply_ips(io, data)
    _save_state(io, data)
