"""
Shell access on a cPanel server. Turning it on or off is the account's login shell, set through WHM (``modifyacct``); the account then signs in with its own hosting password.
Public keys are cPanel's own SSH key list, which needs the "SSH Access" feature in the account's feature list: where it is missing the keys are reported as not manageable from
here (``max_keys`` 0) and the rest still works. No password and no private key is ever read, kept or shown.
"""
import re

from .base import HostingError
from .capabilities import SshCapability, SshKey, SshStatus
from .cpanel_api import CpanelApi

NO_SHELLS = ("noshell", "nologin", "false", "")


class CpanelSsh(CpanelApi, SshCapability):
    def _ssh_port(self, username):
        try:
            return int((self._uapi(username, "SSH", "get_port") or {}).get("port") or 22)
        except (HostingError, TypeError, ValueError):
            return 22

    def _ssh_keys(self, username):
        """(keys, manageable): the public keys cPanel lists for the account, or ([], False) when the account may not use the SSH feature."""
        try:
            found = self._api2(username, "SSH", "listkeys")
        except HostingError:
            return [], False
        keys = []
        for item in found if isinstance(found, list) else []:
            if not isinstance(item, dict) or str(item.get("type") or "").lower() == "private":
                continue
            keys.append(SshKey(fingerprint=str(item.get("name") or item.get("file") or ""), label=str(item.get("name") or item.get("file") or ""),
                               key_type=str(item.get("keytype") or item.get("bits") or "")))
        return [k for k in keys if k.fingerprint], True

    def ssh_access_status(self, username):
        shell = str(self._summary(username).get("shell") or "").rsplit("/", 1)[-1]
        keys, manageable = self._ssh_keys(username)
        return SshStatus(enabled=shell not in NO_SHELLS, host=str(self.host), port=self._ssh_port(username), keys=keys, max_keys=20 if manageable else 0)

    def set_ssh_access(self, username, enabled):
        self._call("modifyacct", {"user": username, "HASSHELL": 1 if enabled else 0})
        self._forget(("summary", username))

    def add_ssh_access_key(self, username, label, public_key):
        key = (public_key or "").strip()
        name = re.sub(r"[^A-Za-z0-9_.-]", "-", (label or "").strip())[:40] or "whpanel-key"
        if "PRIVATE KEY" in key or not re.match(r"^(ssh-(rsa|ed25519|dss)|ecdsa-sha2-nistp\d+|sk-ssh-ed25519@openssh\.com) [A-Za-z0-9+/=]+( .*)?$", key):
            raise HostingError("Paste a public key (it starts with ssh-ed25519 or ssh-rsa). Never paste a private key.")
        self._uapi(username, "SSH", "import_key", {"name": name, "key": key}, secret=True)
        self._uapi(username, "SSH", "authkey", {"key": name, "action": "authorize"})
        return name

    def remove_ssh_access_key(self, username, key_id):
        name = (key_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", name):
            raise HostingError("That key name is not valid.")
        self._uapi(username, "SSH", "authkey", {"key": name, "action": "deauthorize"})
        self._api2(username, "SSH", "delkey", {"key": name, "pub": 1})
