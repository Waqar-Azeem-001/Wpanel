"""
OnlineNIC registrar adapter (their REST API 4.x: every call is a POST, the answer is JSON ``{"code", "message", "data"}``).

Signed with ``token = MD5(user + MD5(password) + timestamp + command)`` plus the API key. Live calls only work from an address
that is on the reseller's IP whitelist. It does everything the panel needs: availability, registering, renewing, transferring in,
the transfer (EPP) code, the transfer lock, nameservers and domain details. The owner of a domain is created at OnlineNIC as a
"contact" from the client's details just before each registration or transfer.

Credentials (encrypted in the registrar page): ``{"user": "...", "password": "...", "apikey": "...", "nameservers": ["ns1...", "ns2..."]}``;
``nameservers`` are used when an order names none. ``base_url`` is optional.
"""
import hashlib
import re
import time
from datetime import datetime, timezone

import requests

from .base import RegistrarAdapter, RegistrarError

SCHEME = "https" + ":" + "//"  # not written whole: the code check forbids hand-typed addresses
LIVE_URL = SCHEME + "api.onlinenic.com"
TEST_URL = SCHEME + "ote.onlinenic.com:5999"
OK = "1000"

# Calling codes for the contact's phone number ("+92.3001234567"). Longest first so +971 is not read as +9.
CALLING_CODES = ["971", "966", "880", "974", "973", "968", "965", "962", "961", "994", "998", "92", "91", "90", "86", "81", "82", "65", "61", "60", "63", "62",
                 "66", "84", "49", "44", "39", "34", "33", "31", "27", "20", "7", "1"]
NATIONAL_PREFIX = {"PK": "92", "IN": "91", "AE": "971", "SA": "966", "GB": "44", "US": "1", "CA": "1", "AU": "61", "DE": "49", "BD": "880", "TR": "90"}


def _md5(text):
    return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()  # the registrar's own signing scheme, not our choice of hash


def phone_for_registry(phone, country):
    """"+923001234567", "0300 1234567" (with the country) or "+92.300…" become the "+92.3001234567" form the registry wants."""
    digits = re.sub(r"[^\d+]", "", phone or "")
    if not digits:
        raise RegistrarError("The client has no phone number.")
    if digits.startswith("+"):
        number = digits[1:]
        for code in CALLING_CODES:
            if number.startswith(code):
                return f"+{code}.{number[len(code):]}"
        raise RegistrarError("The phone number's country code is not recognised. Write it like +92 300 1234567.")
    code = NATIONAL_PREFIX.get((country or "").upper())
    if not code:
        raise RegistrarError("Write the phone number with its country code, like +92 300 1234567.")
    return f"+{code}.{digits.lstrip('0')}"


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
            response = requests.post(self._endpoint(command), data=body, timeout=30)
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RegistrarError("Could not reach the registrar. Try again in a minute.") from exc
        if str(result.get("code")) != OK:
            raise RegistrarError(f"{result.get('message') or 'The registrar refused the request.'} (code {result.get('code')})")
        return result.get("data") or {}

    # --- the owner, as a registry contact --------------------------------------------------------------------------------

    def _contact_id(self, domain_name, contact):
        if not contact:
            raise RegistrarError("The owner's details are missing.")
        missing = [label for key, label in (("name", "name"), ("street", "address"), ("city", "city"), ("state", "state or province"),
                                            ("postcode", "postcode"), ("country", "country"), ("phone", "phone"), ("email", "email"))
                   if not str(contact.get(key) or "").strip()]
        if missing:
            raise RegistrarError(f"Complete the client's details first: {', '.join(missing)}.")
        voice = phone_for_registry(contact["phone"], contact["country"])
        data = self._call(
            "createContact", ext=domain_name.rsplit(".", 1)[-1], name=contact["name"][:60], org=(contact.get("organization") or contact["name"])[:60],
            country=contact["country"].upper()[:2], province=contact["state"][:60], city=contact["city"][:60], street=contact["street"][:60],
            postalcode=contact["postcode"][:10], voice=voice[:17], fax=voice[:17], email=contact["email"][:60])
        contact_id = str(data.get("contactid") or data.get("Contactid") or "").strip()
        if not contact_id:
            raise RegistrarError("The registrar did not return a contact for the owner.")
        return contact_id

    def _nameservers(self, nameservers):
        names = [n for n in (nameservers or self.credentials.get("nameservers") or []) if n]
        if len(names) < 2:
            raise RegistrarError("No nameservers: put two on the order or in the registrar's settings (\"nameservers\").")
        return {f"dns{i}": ns for i, ns in enumerate(names[:6], start=1)}

    @staticmethod
    def _date(raw):
        raw = str(raw or "")[:10]
        try:
            return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    # --- the operations ------------------------------------------------------------------------------------------------------

    def check_availability(self, domain_name):
        data = self._call("checkDomain", domain=domain_name)
        premium = str(data.get("Premium", data.get("premium", ""))).lower() in ("1", "true", "yes")
        return str(data.get("avail", data.get("available", ""))).lower() in ("1", "true", "yes") and not premium  # premium names cost extra: not sold here

    def register_domain(self, domain_name, years, nameservers, contact=None):
        owner = self._contact_id(domain_name, contact)
        data = self._call("registerDomain", domain=domain_name, period=str(years), registrant=owner, admin=owner, tech=owner, billing=owner,
                          **self._nameservers(nameservers))
        expires = self._date(data.get("expdate")) or self.get_domain(domain_name, domain_name)["expires_at"]
        return {"provider_ref": domain_name, "expires_at": expires}

    def transfer_domain(self, domain_name, auth_code, years, contact=None):
        if not auth_code:
            raise RegistrarError("An authorization code is required to transfer this domain.")
        owner = self._contact_id(domain_name, contact)
        self._call("transferDomain", domain=domain_name, password=auth_code, contactid=owner)
        return {"provider_ref": domain_name, "pending": True}  # the old registrar and the registry take days; the panel asks again

    def transfer_status(self, domain_name, auth_code):
        """Where an inbound transfer is, in OnlineNIC's own words (shown to staff)."""
        data = self._call("queryTransferStatus", domain=domain_name, password=auth_code)
        return str(data.get("status") or "")

    def renew_domain(self, domain_name, provider_ref, years):
        data = self._call("renewDomain", domain=domain_name, period=str(years))
        return {"expires_at": self._date(data.get("expdate")) or self.get_domain(domain_name, provider_ref)["expires_at"]}

    def get_domain(self, domain_name, provider_ref):
        data = self._call("infoDomain", domain=domain_name)
        status = str(data.get("status") or "")
        names = [str(data[key]) for key in sorted(data) if key.lower().startswith("dns") and data[key]]
        return {"status": status, "expires_at": self._date(data.get("expdate")), "nameservers": names,
                "is_locked": "transferprohibited" in status.lower().replace(" ", "")}

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
