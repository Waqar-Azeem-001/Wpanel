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
import requests

from .base import HostingAdapter, HostingError


class WhmApiAdapter(HostingAdapter):
    def _base_url(self):
        scheme = "https" if self.use_ssl else "http"
        return f"{scheme}://{self.host}:{self.port}/json-api"

    def _call(self, function, params=None):
        headers = {"Authorization": f"whm {self.credentials.get('api_username', '')}:"
                                    f"{self.credentials.get('api_token', '')}"}
        query = {"api.version": 1, **(params or {})}
        try:
            response = requests.get(f"{self._base_url()}/{function}", headers=headers, params=query,
                                    timeout=self.timeout, verify=self.verify_ssl)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise HostingError(f"Could not reach the server: {exc}") from exc
        except ValueError as exc:
            raise HostingError("The server returned an unexpected (non-JSON) response.") from exc

        metadata = payload.get("metadata") or {}
        if metadata.get("result") != 1:
            raise HostingError(metadata.get("reason") or f"{function} failed.")
        return payload

    def create_account(self, *, username, domain, package, contact_email, password):
        self._call("createacct", {"username": username, "domain": domain, "plan": package,
                                  "contactemail": contact_email, "password": password})
        # WHM's own username is the natural, stable reference for this account.
        return {"provider_ref": username}

    def suspend_account(self, username, reason=""):
        self._call("suspendacct", {"user": username, "reason": reason})

    def unsuspend_account(self, username):
        self._call("unsuspendacct", {"user": username})

    def terminate_account(self, username, *, keep_dns=False):
        self._call("removeacct", {"user": username, "keepdns": 1 if keep_dns else 0})

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
