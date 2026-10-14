"""
Reading products and prices from a WHMCS installation through WHMCS's own API (``GetProducts``), so a server's plans (features) and a billing
system's prices can be put together in WHPanel.

The API identifier and secret are used for this one request and are never stored, logged or put in an error message. WHMCS only answers
callers whose IP address is on its API allow-list, and it names the address it refused, which this module passes on so staff know what to add.
Only https addresses are accepted, redirects are not followed, and an address that points inside a private network is refused (outside of
debug mode), so this cannot be used to make the server call something it should not.
"""
import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit

import requests
from django.conf import settings
from apps.branding.services import store_currency

TIMEOUT = 25
# WHMCS price key -> (WPanel billing cycle, months for a custom cycle, WHMCS setup-fee key)
CYCLES = [("monthly", "monthly", 0, "msetupfee"), ("quarterly", "quarterly", 0, "qsetupfee"), ("semiannually", "semi_annual", 0, "ssetupfee"),
          ("annually", "annual", 0, "asetupfee"), ("biennially", "biennial", 0, "bsetupfee"), ("triennially", "custom", 36, "tsetupfee")]


class WhmcsError(Exception):
    """WHMCS could not be reached, refused the login, or answered in a way we do not understand. The message is safe to show."""


def endpoint(address):
    """The API address for a WHMCS site address, checked: https only, a real host name, and nothing inside a private network."""
    address = (address or "").strip()
    parts = urlsplit(address if "://" in address else "//" + address)  # a bare host name means https
    scheme = parts.scheme or "https"
    try:
        port = parts.port
    except ValueError:
        port = -1
    if scheme != "https" or not parts.hostname or port == -1 or not re.fullmatch(r"[A-Za-z0-9.-]+", parts.hostname) or parts.username:
        raise WhmcsError("Enter the address of your WHMCS site, starting with https.")
    if not settings.DEBUG:
        try:
            infos = socket.getaddrinfo(parts.hostname, port or 443, proto=socket.IPPROTO_TCP)
        except OSError:
            raise WhmcsError("That address could not be found.")
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise WhmcsError("That address points inside a private network, so it was not contacted.")
    path = parts.path.rstrip("/")
    path = path[: -len("/includes/api.php")] if path.endswith("/includes/api.php") else path
    return urlunsplit((scheme, parts.netloc, path + "/includes/api.php", "", ""))


def _call(address, identifier, secret, action, **params):
    if not (identifier and secret):
        raise WhmcsError("Enter the API identifier and the API secret.")
    try:
        response = requests.post(endpoint(address), data={"identifier": identifier, "secret": secret, "action": action, "responsetype": "json", **params},
                                 timeout=TIMEOUT, allow_redirects=False)
    except requests.RequestException as exc:
        raise WhmcsError("WHMCS could not be reached: " + re.sub(r"\?[^\s)'\"]*", "", str(exc))[:200]) from exc
    if 300 <= response.status_code < 400:
        raise WhmcsError("WHMCS redirected the request. Use the exact address of your WHMCS site (check http/https and www).")
    try:
        payload = response.json()
    except ValueError:
        raise WhmcsError("WHMCS did not answer with an API response. Check the address (it should be the folder WHMCS is installed in).")
    if payload.get("result") != "success":
        message = str(payload.get("message") or "WHMCS refused the request.")[:300]
        if "invalid ip" in message.lower():
            message += " Add that address to WHMCS under Configuration > System Settings > General Settings > Security > API IP Access Restriction."
        raise WhmcsError(message)
    return payload


def _money(value):
    """A WHMCS amount as text, or None when WHMCS says the cycle is not offered (-1.00) or the amount is not a number."""
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return None if number < 0 else f"{number:.2f}"


def fetch_products(address, identifier, secret, currency=""):
    """
    ``(rows, currency, currencies)``: one row per WHMCS product with its prices in one currency (the store's if WHMCS has it, else WHMCS's
    first), as ``{"pid", "name", "group", "paytype", "module", "prices": {cycle: {"price", "setup", "custom_months"}}}``.
    """
    payload = _call(address, identifier, secret, "GetProducts")
    items = ((payload.get("products") or {}).get("product")) or []
    if isinstance(items, dict):
        items = [items]
    codes = []
    for item in items:
        for code in (item.get("pricing") or {}):
            if code not in codes:
                codes.append(code)
    wanted = currency or store_currency()
    chosen = wanted if wanted in codes else (codes[0] if codes else wanted)
    rows = []
    for item in items:
        pricing = (item.get("pricing") or {}).get(chosen) or {}
        prices = {}
        for whmcs_key, cycle, months, setup_key in CYCLES:
            amount = _money(pricing.get(whmcs_key))
            if amount is not None and float(amount) > 0:
                prices[cycle if not months else f"custom:{months}"] = {"price": amount, "setup": _money(pricing.get(setup_key)) or "0.00",
                                                                      "cycle": cycle, "custom_months": months}
        rows.append({"pid": str(item.get("pid", "")), "name": str(item.get("name", "")).strip(), "group": str(item.get("groupname") or item.get("gid") or ""),
                     "paytype": str(item.get("paytype", "")), "module": str(item.get("module", "")), "prices": prices})
    return rows, chosen, codes


def normal(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())
