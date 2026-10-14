"""
The demo provider's security tools. They work on the account's real (sandbox) website files:

* Malware scan: reads each web file (up to 5,000 files, 1 MB each) and reports known bad patterns (obfuscated code, code that runs
  what a visitor sends, well-known shell names, PHP files hiding in upload folders, double extensions). It is a pattern check, not
  proof; it reports and never deletes.
* Quarantine: moves a flagged file out of the website folder (into the account's state folder) and can put it back.
* Directory protection: a folder that asks for a login; only a hash of the password is kept.
* SSH: switch on or off, and public keys that are checked properly (type, structure, RSA size) and shown by fingerprint.
* File-integrity monitoring: a baseline of every file's checksum, and a check that lists what changed, appeared or vanished.
No traffic is filtered and no SSH service runs here; a real provider (the cPanel adapter) implements the same
``SecurityCapability`` contract.
"""
import base64
import hashlib
import json
import re
import shutil
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path

from django.contrib.auth.hashers import make_password

from .. import scan_rules
from .base import HostingError
from .capabilities import (IntegrityResult, ProtectedDir, QuarantineItem, ScanFinding, ScanResult, SshKey, SshStatus)

MAX_KEYS = 5
MAX_PROTECTED = 10
SKIP = {".trash"}
WEB_EXTENSIONS = set(scan_rules.WEB_EXTENSIONS)
SHELL_NAMES = set(scan_rules.SHELL_NAMES)
UPLOAD_DIRS = set(scan_rules.UPLOAD_DIRS)
MAX_SCAN_FILES, MAX_SCAN_BYTES = scan_rules.MAX_SCAN_FILES, scan_rules.MAX_SCAN_BYTES
PATTERNS, SEVERITY_ORDER = scan_rules.PATTERNS, scan_rules.SEVERITY_ORDER
KEY_TYPES = {"ssh-ed25519": 0, "ssh-rsa": 2048, "ecdsa-sha2-nistp256": 0, "ecdsa-sha2-nistp384": 0, "ecdsa-sha2-nistp521": 0}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data), encoding="utf-8")


def parse_public_key(text):
    """(type, fingerprint) of an OpenSSH public key, or HostingError saying what is wrong with it."""
    text = (text or "").strip()
    if "PRIVATE KEY" in text or "BEGIN" in text:
        raise HostingError("That looks like a private key. Never share it: paste only the public key (the .pub file).")
    parts = text.split()
    if len(parts) < 2 or "\n" in text or len(text) > 8192:
        raise HostingError("Paste one public key on a single line, such as: ssh-ed25519 AAAA... comment")
    kind, blob = parts[0], parts[1]
    if kind not in KEY_TYPES:
        raise HostingError("Use an ed25519, RSA or ECDSA key (ssh-ed25519, ssh-rsa, ecdsa-sha2-...).")
    try:
        raw = base64.b64decode(blob, validate=True)
        fields, offset = [], 0
        while offset < len(raw):
            (size,) = struct.unpack(">I", raw[offset:offset + 4])
            fields.append(raw[offset + 4:offset + 4 + size])
            if len(fields[-1]) != size:
                raise ValueError
            offset += 4 + size
    except (ValueError, struct.error):
        raise HostingError("That public key is damaged or incomplete. Copy it again from the .pub file.")
    if not fields or fields[0].decode("ascii", "ignore") != kind:
        raise HostingError("That public key is damaged: its type does not match its contents.")
    if kind == "ssh-rsa":
        if len(fields) != 3 or int.from_bytes(fields[2], "big").bit_length() < KEY_TYPES["ssh-rsa"]:
            raise HostingError("RSA keys must be at least 2048 bits. Make a new key, preferably ed25519.")
    elif kind == "ssh-ed25519" and (len(fields) != 2 or len(fields[1]) != 32):
        raise HostingError("That ed25519 key is not valid.")
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    return kind, fingerprint


class DemoSecurity:
    def __init__(self, state_dir, site_dir, domain):
        self.state = Path(state_dir)
        self.site = Path(site_dir)
        self.domain = domain

    # --- walking the website -----------------------------------------------------------------------------------------

    def _files(self):
        count = 0
        if not self.site.exists():
            return
        for path in sorted(self.site.rglob("*")):
            rel = path.relative_to(self.site)
            if rel.parts[0] in SKIP or path.is_symlink() or not path.is_file():
                continue
            count += 1
            if count > MAX_SCAN_FILES:
                return
            yield rel.as_posix(), path

    def _site_path(self, rel, *, must_be="file"):
        rel = (rel or "").strip().strip("/")
        parts = rel.split("/")
        if not rel or ".." in parts or "\\" in rel or "\x00" in rel or parts[0] in SKIP:
            raise HostingError("That location is not inside your website's folder.")
        target = (self.site / rel)
        root = self.site.resolve()
        resolved = target.resolve()
        if root not in resolved.parents and resolved != root or any((root / "/".join(parts[:i + 1])).is_symlink() for i in range(len(parts))):
            raise HostingError("That location is not inside your website's folder.")
        if must_be == "file" and not resolved.is_file():
            raise HostingError("That file does not exist.")
        if must_be == "dir" and not resolved.is_dir():
            raise HostingError("That folder does not exist.")
        return resolved

    # --- malware scan ------------------------------------------------------------------------------------------------

    def _scan_file(self, rel, path):
        findings = []
        name, suffix = path.name.lower(), path.suffix.lower()
        parts = [p.lower() for p in rel.split("/")[:-1]]
        if name in SHELL_NAMES:
            findings.append(ScanFinding(rel, "known-shell-name", "high", "This file has the name of a well-known hacker tool."))
        if re.search(r"\.(php\d?|phtml)\.(jpe?g|png|gif|txt|ico)$", name):
            findings.append(ScanFinding(rel, "double-extension", "medium", "A script disguised as a picture or text file."))
        if suffix in (".php", ".phtml", ".php5") and any(p in UPLOAD_DIRS for p in parts):
            findings.append(ScanFinding(rel, "script-in-uploads", "medium",
                                        "A PHP file in a folder meant for uploads. Uploads should never be programs."))
        if name.startswith(".") and suffix in (".php", ".phtml"):
            findings.append(ScanFinding(rel, "hidden-script-file", "medium", "A hidden PHP file."))
        if suffix in WEB_EXTENSIONS or name == ".htaccess" or ".php" in name:
            try:
                data = path.read_bytes()[:MAX_SCAN_BYTES]
            except OSError:
                data = b""
            for rule, severity, pattern, detail in PATTERNS:
                if pattern.search(data):
                    findings.append(ScanFinding(rel, rule, severity, detail))
        return findings

    def scan_result(self):
        data = _read_json(self.state / "scan.json", None)
        if not data:
            return ScanResult()
        return ScanResult(scanned_at=data["scanned_at"], files_scanned=data["files_scanned"],
                          findings=[ScanFinding(**f) for f in data["findings"]])

    def scan(self):
        findings, scanned = [], 0
        for rel, path in self._files():
            scanned += 1
            findings += self._scan_file(rel, path)
        findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.path, f.rule))
        data = {"scanned_at": _now(), "files_scanned": scanned,
                "findings": [f.__dict__ for f in findings]}
        _write_json(self.state / "scan.json", data)
        return self.scan_result()

    # --- quarantine --------------------------------------------------------------------------------------------------

    def _quarantine_index(self):
        return _read_json(self.state / "quarantine" / "index.json", [])

    def quarantine(self, rel):
        path = self._site_path(rel)
        rel = path.relative_to(self.site.resolve()).as_posix()
        finding = next((f for f in self.scan_result().findings if f.path == rel), None)
        item_id = uuid.uuid4().hex[:10]
        folder = self.state / "quarantine"
        folder.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(folder / f"{item_id}.file"))
        rows = self._quarantine_index()
        rows.insert(0, {"id": item_id, "path": rel, "rule": finding.rule if finding else "", "at": _now()})
        _write_json(folder / "index.json", rows)
        self._drop_findings(rel)
        return item_id

    def _drop_findings(self, rel):
        data = _read_json(self.state / "scan.json", None)
        if data:
            data["findings"] = [f for f in data["findings"] if f["path"] != rel]
            _write_json(self.state / "scan.json", data)

    def quarantine_list(self):
        return [QuarantineItem(item_id=r["id"], path=r["path"], rule=r["rule"], quarantined_at=r["at"]) for r in self._quarantine_index()]

    def restore(self, item_id):
        rows = self._quarantine_index()
        row = next((r for r in rows if r["id"] == item_id), None)
        if row is None:
            raise HostingError("That item is not in quarantine any more.")
        target = self.site / row["path"]
        if target.exists():
            raise HostingError("A file with that name exists again, so this one was not put back. Rename or remove the new one first.")
        if not target.parent.is_dir():
            raise HostingError("The folder this file came from no longer exists, so it was not put back.")
        shutil.move(str(self.state / "quarantine" / f"{item_id}.file"), str(target))
        _write_json(self.state / "quarantine" / "index.json", [r for r in rows if r["id"] != item_id])

    # --- directory protection ----------------------------------------------------------------------------------------

    def protected(self):
        return [ProtectedDir(path=r["path"], login=r["login"], created_at=r["created_at"]) for r in _read_json(self.state / "protected.json", [])]

    def protect(self, rel, login, password):
        path = self._site_path(rel, must_be="dir")
        rel = path.relative_to(self.site.resolve()).as_posix()
        login = (login or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", login):
            raise HostingError("The login uses 3 to 32 letters, numbers, dots, dashes or underscores.")
        password = password or ""
        if len(password) < 10 or len(password) > 128 or not (re.search(r"[a-z]", password) and re.search(r"[A-Z]", password)
                                                              and re.search(r"\d", password)):
            raise HostingError("Use a password of 10 to 128 characters with upper and lower case letters and numbers.")
        rows = _read_json(self.state / "protected.json", [])
        rows = [r for r in rows if r["path"] != rel]  # protecting again replaces the old login
        if len(rows) >= MAX_PROTECTED:
            raise HostingError(f"You can protect up to {MAX_PROTECTED} folders. Remove one to add another.")
        rows.append({"path": rel, "login": login, "password_hash": make_password(password), "created_at": _now()})
        _write_json(self.state / "protected.json", rows)

    def unprotect(self, rel):
        rel = (rel or "").strip().strip("/")
        rows = _read_json(self.state / "protected.json", [])
        if not any(r["path"] == rel for r in rows):
            raise HostingError("That folder is not protected.")
        _write_json(self.state / "protected.json", [r for r in rows if r["path"] != rel])

    # --- SSH ---------------------------------------------------------------------------------------------------------

    def _ssh(self):
        data = _read_json(self.state / "ssh.json", {})
        return {"enabled": bool(data.get("enabled")), "keys": data.get("keys", [])}

    def ssh_status(self):
        data = self._ssh()
        return SshStatus(enabled=data["enabled"], host=f"ssh.{self.domain}", port=22, max_keys=MAX_KEYS,
                         keys=[SshKey(fingerprint=k["fingerprint"], label=k["label"], key_type=k["type"], added_at=k["added_at"])
                               for k in data["keys"]])

    def set_ssh(self, enabled):
        data = self._ssh()
        if enabled and not data["keys"]:
            raise HostingError("Add a public key first: SSH here signs in with keys, not passwords.")
        data["enabled"] = bool(enabled)
        _write_json(self.state / "ssh.json", data)

    def add_key(self, label, public_key):
        label = (label or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9 _.@-]{1,40}", label):
            raise HostingError("Give the key a short name (letters, numbers, spaces, dots, dashes), such as My laptop.")
        kind, fingerprint = parse_public_key(public_key)
        data = self._ssh()
        if any(k["fingerprint"] == fingerprint for k in data["keys"]):
            raise HostingError("That key is already added.")
        if len(data["keys"]) >= MAX_KEYS:
            raise HostingError(f"You can add up to {MAX_KEYS} keys. Remove one to add another.")
        data["keys"].append({"fingerprint": fingerprint, "label": label, "type": kind, "added_at": _now()})
        _write_json(self.state / "ssh.json", data)
        return fingerprint

    def delete_key(self, fingerprint):
        data = self._ssh()
        if not any(k["fingerprint"] == fingerprint for k in data["keys"]):
            raise HostingError("That key no longer exists.")
        data["keys"] = [k for k in data["keys"] if k["fingerprint"] != fingerprint]
        if not data["keys"]:
            data["enabled"] = False  # SSH without any key cannot be used, so it goes off with the last key
        _write_json(self.state / "ssh.json", data)

    # --- file-integrity monitoring -----------------------------------------------------------------------------------

    def _hashes(self):
        return {rel: _sha256(path) for rel, path in self._files() if not rel.startswith("logs/")}

    def integrity_status(self):
        baseline = _read_json(self.state / "integrity.json", None)
        if not baseline:
            return IntegrityResult()
        last = baseline.get("last") or {}
        return IntegrityResult(baseline_at=baseline["at"], files_in_baseline=len(baseline["files"]), checked_at=last.get("at"),
                               changed=last.get("changed", []), added=last.get("added", []), removed=last.get("removed", []))

    def baseline(self):
        _write_json(self.state / "integrity.json", {"at": _now(), "files": self._hashes()})
        return self.integrity_status()

    def check(self):
        baseline = _read_json(self.state / "integrity.json", None)
        if not baseline:
            raise HostingError("Take a baseline first: it records how your files look now, so later changes can be found.")
        now = self._hashes()
        old = baseline["files"]
        baseline["last"] = {"at": _now(), "changed": sorted(p for p in now if p in old and now[p] != old[p]),
                            "added": sorted(set(now) - set(old)), "removed": sorted(set(old) - set(now))}
        _write_json(self.state / "integrity.json", baseline)
        return self.integrity_status()
