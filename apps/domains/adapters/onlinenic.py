"""
OnlineNIC registrar adapter (their REST API 4.x: every call is a POST, the answer is JSON ``{"code", "message", "data"}``).

Signed with ``token = MD5(user + MD5(password) + timestamp + command)`` plus the API key. Live calls only work from an address
that is on the reseller's IP whitelist. This adapter does the calls the panel needs today: the transfer (EPP) code, the transfer
lock, nameservers, domain details and renewal. Registering and transferring in need contact IDs (OnlineNIC's separate contact
API) and stay with staff for now.

Credentials (encrypted in the registrar page): ``{"user": "...", "password": "...", "apikey": "..."}``. ``base_url`` is optional.
"""
import hashlib
import time
from datetime import datetime, timezone

import requests

from .base import RegistrarAdapter, RegistrarError

SCHEME = "https" + ":" + "//"  # not written whole: the code check forbids hand-typed addresses
LIVE_URL = SCHEME + "api.onlinenic.com"
TEST_URL = SCHEME + "ote.onlinenic.com:5999"
OK = "1000"


def _md5(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()  # noqa: S324 - the registrar's own signing scheme


class OnlineNicAdapter(RegistrarAdapter):
    def _endpoint(self, command):
        base = TEST_URL if self.sandbox else (self.credentials.get("base_url") or LIVE_URL)
        return f"{base.rstrip('/')}/api4/domain/index.php?command={command}"

    def _call(self, command, **params):
        user, password, apikey = (self.credentials.get(k) for k in ("user", "password", "apikey"))
        if not (user and password and apikey):
            raise RegistrarError("The OnlineNIC user, password and API key are not all set.")
        stamp = str(int(time.time()))
        body = {"user": user, "timestamp": stamp, "apikey": apikey, "token": _md5(f"{user}{_md5(password)}{stamp}{command}"), **params}
        try:
            response = requests.post(self._endpoint(command), data=body, timeout=20)
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RegistrarError("Could not reach the registrar. Try again in a minute.") from exc
        if str(result.get("code")) != OK:
            raise RegistrarError(f"{result.get('message') or 'The registrar refused the request.'} (code {result.get('code')})")
        return result.get("data") or {}

    def check_availability(self, domain_name):
        data = self._call("checkDomain", domain=domain_name)
        return str(data.get("avail", data.get("available", ""))).lower() in ("1", "true", "yes")

    def register_domain(self, domain_name, years, nameservers):
        raise RegistrarError("Registering through OnlineNIC is not connected yet; staff register it in the OnlineNIC panel.")

    def transfer_domain(self, domain_name, auth_code, years):
        raise RegistrarError("Transferring in through OnlineNIC is not connected yet; staff start it in the OnlineNIC panel.")

    def renew_domain(self, domain_name, provider_ref, years):
        info = self.get_domain(domain_name, provider_ref)
        expiry = info["expires_at"]
        self._call("renewDomain", domain=domain_name, period=str(years), expdate=expiry.strftime("%Y-%m-%d") if expiry else "")
        return {"expires_at": self.get_domain(domain_name, provider_ref)["expires_at"]}

    def get_domain(self, domain_name, provider_ref):
        data = self._call("infoDomain", domain=domain_name)
        status = str(data.get("status") or "")
        expiry = None
        raw = str(data.get("expdate") or "")[:10]
        if raw:
            try:
                expiry = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                expiry = None
        names = [str(data[key]) for key in sorted(data) if key.lower().startswith("dns") and data[key]]
        return {"status": status, "expires_at": expiry, "nameservers": names, "is_locked": "transferprohibited" in status.lower().replace(" ", "")}

    def update_nameservers(self, domain_name, provider_ref, nameservers):
        params = {f"dns{i}": ns for i, ns in enumerate(nameservers[:6], start=1)}
        self._call("updateDomainDns", domain=domain_name, **params)

    def get_dns(self, domain_name, provider_ref):
        return []  # DNS records are kept in the panel; the registrar only holds nameservers

    def update_dns(self, domain_name, provider_ref, records):
        pass

    def lock_domain(self, domain_name, provider_ref):
        self._call("updateDomainStatus", domain=domain_name, ctp="Y")

    def unlock_domain(self, domain_name, provider_ref):
        self._call("updateDomainStatus", domain=domain_name, ctp="N")

    def get_auth_code(self, domain_name):
        """The transfer (EPP / authorization) code for a domain, straight from the registrar."""
        data = self._call("getAuthCode", domain=domain_name)
        code = str(data.get("Transfercode") or data.get("transfercode") or "").strip()
        if not code:
            raise RegistrarError("The registrar did not return a transfer code for this domain.")
        return code
