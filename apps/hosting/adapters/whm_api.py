"""
Real adapter for cPanel/WHM's public JSON API (api.docs.cpanel.net/whm/1/),
authenticating with a WHM API Token (``Authorization: whm user:token`` - the
current recommended method; not the root password).

Unlike a domain registrar, WHM is a single, stable, publicly documented
protocol, so this makes real HTTP calls rather than simulating locally - see
``apps.hosting.adapters.manual`` for the simulation used until a server is
configured.

Written against cPanel's published documentation. IMPORTANT: it has not been
exercised against a live WHM/cPanel server in this environment (none was
available) - test it against a real server before production use. The
account-lifecycle calls (create/suspend/unsuspend/terminate/change package)
use WHM API 1's standard ``metadata.result``/``metadata.reason`` envelope,
which is stable and documented across all API 1 functions. ``get_usage()`` is
the part most likely to need adjustment: the exact field names WHM reports
have varied across versions, so it is parsed defensively and falls back to
``None`` (unknown) rather than guessing.
"""
import re

import requests

from .base import HostingAdapter, HostingError
from .capabilities import Capability, NodeCapability, NodeService, NodeStatus
from .cpanel_backups import CpanelBackups
from .cpanel_cron import CpanelCron
from .cpanel_api import plan_limit
from .cpanel_dns import CpanelDns
from .cpanel_domains import CpanelDomains
from .cpanel_email import CpanelEmail
from .cpanel_files import CpanelFiles
from .cpanel_ftp import CpanelFtp
from .cpanel_logs import CpanelLogs
from .cpanel_mysql import CpanelMysql
from .cpanel_native import CpanelNative
from .cpanel_php import CpanelPhp
from .cpanel_ssl import CpanelSsl

# (key, label, WHM service names): the node is reported as running only when every named service is
SERVICES = [("web", "Web server", ["httpd"]), ("database", "Database", ["mysql"]), ("mail", "Mail", ["exim", "dovecot"]),
            ("dns", "DNS", ["named"])]


class WhmApiAdapter(CpanelFiles, CpanelFtp, CpanelLogs, CpanelDomains, CpanelDns, CpanelSsl, CpanelMysql, CpanelEmail, CpanelPhp, CpanelCron, CpanelBackups, CpanelNative,
                    NodeCapability, HostingAdapter):
    read_cache_seconds = 45  # a real server is slow to answer, so what the gateway has just read is remembered for a few seconds

    @property
    def enabled_capabilities(self):
        """
        Which customer tools this cPanel server may offer. The cPanel mappings have not been run against a live server, so none is on
        until staff list it in ``HOSTING_CPANEL_CAPABILITIES`` after trying it on a staging server (see docs/h14-cpanel-adapter.md).
        """
        from django.conf import settings

        return frozenset(Capability(c) for c in getattr(settings, "HOSTING_CPANEL_CAPABILITIES", []) if c in {x.value for x in Capability})

    def _base_url(self):
        scheme = "https" if self.use_ssl else "http"
        return f"{scheme}://{self.host}:{self.port}/json-api"

    def _call(self, function, params=None, *, sensitive=False, raw=False):
        """
        ``sensitive=True`` (only ``create_account``, which carries a new password) sends ``params`` as a POST body
        instead of a GET query string, so it never appears in this server's own request URL - which the WHM/Apache
        access log on the *other* end would otherwise record in the clear, and which a network error's own message
        would otherwise quote back at us (see ``_redact_url`` below, for defence in depth on that second part).
        """
        headers = {"Authorization": f"whm {self.credentials.get('api_username', '')}:"
                                    f"{self.credentials.get('api_token', '')}"}
        query = {"api.version": 1}
        body = None
        if sensitive:
            body = params or {}
        else:
            query.update(params or {})
        try:
            request = requests.post if sensitive else requests.get
            for attempt in (1, 2):
                try:
                    response = request(f"{self._base_url()}/{function}", headers=headers, params=query, data=body,
                                       timeout=self.timeout, verify=self.verify_ssl)
                    break
                except requests.exceptions.Timeout:
                    # A slow answer is common on a busy server. A read is safe to ask for again; anything that might change something is never
                    # repeated, because the first attempt may already have gone through (and a second delete or create would then fail).
                    if sensitive or attempt == 2 or not is_read(function, params):
                        raise
            if response.status_code in (401, 403):
                raise HostingError("The server refused the login. Check the API username and the API token, and that the "
                                   "token is allowed to do this (WHM > Manage API Tokens).")
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            note = " The change may still have been made on the server: reload the page to check." if not is_read(function, params) and \
                isinstance(exc, requests.exceptions.Timeout) else ""
            raise HostingError(f"Could not reach the server: {_describe(exc)}{note}") from exc
        except ValueError as exc:
            raise HostingError("The server returned an unexpected (non-JSON) response.") from exc

        if raw:  # WHM's ``cpanel`` function answers with the module's own ``cpanelresult``, not the usual metadata envelope
            return payload
        metadata = payload.get("metadata") or {}
        if metadata.get("result") != 1:
            raise HostingError(metadata.get("reason") or f"{function} failed.")
        return payload

    def check_connection(self):
        """Read-only: the WHM version, the hosting packages it offers and what each includes (disk, bandwidth, mailboxes, databases...)."""
        version = (self._call("version").get("data") or {}).get("version") or ""
        packages = (self._call("listpkgs").get("data") or {}).get("pkg") or []
        details = {p["name"]: package_features(p) for p in packages if p.get("name")}
        return {"version": str(version), "packages": sorted(details), "details": details}

    def node_status(self):
        """
        What WHM can honestly report about the machine: its version, the load average and whether the web, database, mail and DNS
        services run. CPU, memory, disk, network, a node agent and backups are not reported by WHM, so they stay ``None`` /
        "unavailable" instead of being guessed. Parsed defensively, like ``get_usage``; not yet exercised against a live server.
        """
        version = str((self._call("version").get("data") or {}).get("version") or "")
        load = None
        try:
            payload = self._call("systemloadavg")
            data = payload.get("data") if isinstance(payload.get("data"), dict) and "one" in payload["data"] else payload
            load = (float(data["one"]), float(data["five"]), float(data["fifteen"]))
        except (HostingError, KeyError, TypeError, ValueError):
            load = None
        services = []
        for key, label, names in SERVICES:
            running = []
            for name in names:
                try:
                    entries = (self._call("servicestatus", {"service": name}).get("data") or {}).get("service") or []
                    running.append(bool(int(entries[0].get("running", 0))) if entries else None)
                except (HostingError, TypeError, ValueError, IndexError, AttributeError):
                    running.append(None)
            known = [r for r in running if r is not None]  # a check that timed out says nothing either way
            if not known:
                state = "unavailable"
            elif all(known):
                state = "running"
            elif not any(known):
                state = "down"
            else:
                state = "degraded"
            services.append(NodeService(key, label, state))
        services.append(NodeService("agent", "Node agent", "unavailable", "cPanel servers have no WHPanel agent."))
        services.append(NodeService("backups", "Backups", "unavailable", "cPanel's API does not report backup state here."))
        return NodeStatus(version=version, load=load, services=services)

    def create_account(self, *, username, domain, package, contact_email, password):
        self._call("createacct", {"username": username, "domain": domain, "plan": package,
                                  "contactemail": contact_email, "password": password}, sensitive=True)
        # WHM's own username is the natural, stable reference for this account.
        return {"provider_ref": username}

    def change_password(self, username, password):
        """WHM's ``passwd`` function; the new password travels in a POST body, never in a web address."""
        self._call("passwd", {"user": username, "password": password, "db_pass_update": 0}, sensitive=True)

    def suspend_account(self, username, reason=""):
        self._call("suspendacct", {"user": username, "reason": reason})

    def unsuspend_account(self, username):
        self._call("unsuspendacct", {"user": username})

    def terminate_account(self, username, *, keep_dns=False):
        self._call("removeacct", {"user": username, "keepdns": 1 if keep_dns else 0})

    def backup_account(self, username):
        """Requests WHM's own full-account backup (generated asynchronously on the server; this call only starts
        it - WHM's response carries no backup id to track). Like the rest of this adapter, untested against a
        live server - see the module docstring."""
        self._call("fullaccountbackup", {"user": username})
        return {"backup_ref": f"whm-fullaccountbackup-{username}"}

    def change_package(self, username, package):
        self._call("changepackage", {"user": username, "pkg": package})

    def get_status(self, username):
        payload = self._call("accountsummary", {"user": username})
        accounts = (payload.get("data") or {}).get("acct") or []
        if not accounts:
            raise HostingError(f"WHM has no record of account '{username}'.")
        account = accounts[0]
        return {"suspended": bool(int(account.get("suspended", 0) or 0)), "domain": account.get("domain", "")}

    def get_usage(self, username):
        """Best-effort; see the module docstring."""
        result = {"disk_used_mb": None, "disk_limit_mb": None, "bandwidth_used_mb": None, "bandwidth_limit_mb": None}
        try:
            payload = self._call("accountsummary", {"user": username})
            accounts = (payload.get("data") or {}).get("acct") or []
            if accounts:
                result["disk_used_mb"] = _parse_size_mb(accounts[0].get("diskused"))
                result["disk_limit_mb"] = _parse_size_mb(accounts[0].get("disklimit"))
        except HostingError:
            pass
        try:
            payload = self._call("showbw", {"search": username, "searchtype": "user"})
            entries = (payload.get("data") or {}).get("bandwidth") or []
            total_bytes = entries[0].get("totalbytes") if entries else None
            if total_bytes is not None:
                result["bandwidth_used_mb"] = int(total_bytes) // (1024 * 1024)
        except (HostingError, TypeError, ValueError):
            pass
        return result


READ_FUNCTIONS = frozenset({"version", "listpkgs", "servicestatus", "systemloadavg", "dumpzone", "accountsummary", "showbw", "listaccts", "getpkginfo"})
READ_WORDS = ("list", "get", "domains_data", "installed_hosts", "listcron", "php_get", "php_ini_get", "query", "check_")


def is_read(function, params):
    """True for a call that only asks (so it is safe to repeat). WHM's own read functions are listed; a call run as an account (UAPI or API 2) is a
    read only when its function name says so (list..., get..., and a few others). Anything unknown counts as a change, so it is never repeated."""
    if function in READ_FUNCTIONS:
        return True
    name = str((params or {}).get("cpanel.function") or (params or {}).get("cpanel_jsonapi_func") or "")
    return bool(name) and (name.startswith(READ_WORDS) or name in ("domains_data", "installed_hosts", "listcron"))


def package_features(raw):
    """What a WHM package includes, as the neutral feature keys a product uses (WHM spells them QUOTA, MAXPOP, MAXSQL...)."""
    def yes(value):
        return "Yes" if str(value).strip().lower() in ("y", "yes", "1", "true") else "No"

    def size(key):
        return plan_limit(raw.get(key), zero_is_unlimited=True)

    def count(key):
        return plan_limit(raw.get(key))

    return {"disk_mb": size("QUOTA"), "bandwidth_mb": size("BWLIMIT"), "email_accounts": count("MAXPOP"), "databases": count("MAXSQL"),
            "ftp_accounts": count("MAXFTP"), "subdomains": count("MAXSUB"), "parked_domains": count("MAXPARK"), "addon_domains": count("MAXADDON"),
            "mailing_lists": count("MAXLST"), "emails_per_hour": size("MAX_EMAIL_PER_HOUR"), "shell_access": yes(raw.get("HASSHELL")),
            "dedicated_ip": yes(raw.get("IP"))}


def _redact_url(exc):
    """
    A network exception's own message often quotes the request URL back (``requests.ConnectionError``, ``requests.HTTPError``...); if a
    query string ever ended up on it, this is the last line of defence against a password reaching an error message that a Super Admin
    sees on the order/audit history (``createacct`` itself is sent as a POST body precisely so it never has one, but a defence that only
    works for one call site is not a defence). Removes everything from a ``?`` to the end of that address (the next space or bracket),
    and keeps the rest, because the part after the address (``Caused by ...``) is what says WHY the call failed.
    """
    return re.sub(r"\?[^\s)'\"]*", "", str(exc))


def _describe(exc):
    """What went wrong with reaching the server, in plain words, followed by the redacted technical detail."""
    detail = _redact_url(exc)
    lowered = detail.lower()
    if isinstance(exc, requests.exceptions.SSLError) or "certificate verify failed" in lowered or "sslerror" in lowered:
        why = ("The server answered, but its security certificate could not be verified. Use the server's host name (not the IP address) "
               "with a valid certificate, or untick 'verify SSL' on the server for a certificate you trust.")
    elif isinstance(exc, requests.exceptions.Timeout) or "timed out" in lowered or "timeout" in lowered:
        why = ("The server did not answer in time. It may be switched off, or a firewall is dropping this application's connection: "
               "check that the server is running and that this application's public IP address is allowed on the WHM port.")
    elif "name or service not known" in lowered or "getaddrinfo failed" in lowered or "nodename nor servname" in lowered:
        why = "The server's name could not be found. Check the host name saved on the server."
    elif "connection refused" in lowered or "actively refused" in lowered or "winerror 10061" in lowered:
        why = "The server refused the connection. Check the WHM port (usually 2087) and that WHM is running."
    elif "network is unreachable" in lowered or "no route to host" in lowered or "winerror 10051" in lowered or "winerror 10065" in lowered:
        why = "There is no network route to the server from this application."
    else:
        why = ""
    return f"{why} ({detail})" if why else detail


def _parse_size_mb(value):
    """WHM reports sizes like '150M', '2.5G' or 'unlimited'. Returns int MB, or None if unknown/unlimited."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("unlimited", ""):
        return None
    try:
        if text.endswith("g"):
            return int(float(text[:-1]) * 1024)
        if text.endswith("m"):
            return int(float(text[:-1]))
        if text.endswith("k"):
            return max(1, int(float(text[:-1]) / 1024))
        return int(float(text))
    except ValueError:
        return None
