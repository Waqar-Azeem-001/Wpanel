"""
The rules for what a DNS record or a domain name may be, shared by every provider that needs them (the demo provider and the cPanel
adapter): names tidied into the zone, wildcard only as the first label, per-type value checks (IPv4, IPv6, host names, MX and SRV
priority, SRV and CAA syntax), TTL range, CNAME not on the domain itself. A real server checks again; this is the first line, so
nothing odd is ever sent to it.
"""
import ipaddress
import re

from .base import HostingError
from .capabilities import DNS_MAX_TTL, DNS_MIN_TTL, DNS_RECORD_TYPES

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+([a-z]{2,24}|xn--[a-z0-9-]{2,20})$")
LABEL_RE = re.compile(r"^(\*|_?[a-z0-9]([a-z0-9_-]{0,61}[a-z0-9_])?)$")
HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)+[a-z0-9-]{2,24}$")
SRV_NAME_RE = re.compile(r"^_[a-z0-9-]+\._(tcp|udp|tls)(\.[a-z0-9._-]+)?$")
CAA_RE = re.compile(r'^(0|128)\s+(issue|issuewild|iodef)\s+"([^"\n]{0,255})"$')



def clean_domain(name):
    name = (name or "").strip().lower().rstrip(".")
    if not DOMAIN_RE.match(name):
        raise HostingError("Enter a full domain name such as example.com or blog.example.com.")
    return name


def normalize_record(domain, record):
    """The record checked and tidied: (type, name, value, ttl, priority). Raises HostingError with a plain reason."""
    kind = (record.type or "").upper()
    if kind not in DNS_RECORD_TYPES:
        raise HostingError("Choose a record type: " + ", ".join(DNS_RECORD_TYPES) + ".")
    name = (record.name or "@").strip().lower().rstrip(".")
    if name in ("", "@", domain):
        name = "@"
    elif name.endswith("." + domain):
        name = name[: -(len(domain) + 1)]
    if name != "@":
        labels = name.split(".")
        if len(name) > 200 or any(not LABEL_RE.match(label) for label in labels) or ("*" in name and labels[0] != "*") \
                or (name.count("*") > 1):
            raise HostingError("That name is not valid. Use letters, numbers, hyphens and underscores, or @ for the domain itself.")
    try:
        ttl = int(record.ttl)
    except (TypeError, ValueError):
        raise HostingError("The TTL must be a number of seconds.")
    if not DNS_MIN_TTL <= ttl <= DNS_MAX_TTL:
        raise HostingError(f"The TTL must be between {DNS_MIN_TTL} and {DNS_MAX_TTL} seconds.")
    value = (record.value or "").strip()
    priority = None
    if kind == "A":
        try:
            value = str(ipaddress.IPv4Address(value))
        except ValueError:
            raise HostingError("An A record needs an IPv4 address such as 203.0.113.10.")
    elif kind == "AAAA":
        try:
            value = str(ipaddress.IPv6Address(value))
        except ValueError:
            raise HostingError("An AAAA record needs an IPv6 address.")
    elif kind in ("CNAME", "MX"):
        value = value.lower().rstrip(".")
        if not HOST_RE.match(value):
            raise HostingError(f"A {kind} record needs a full host name such as mail.example.com.")
        if kind == "CNAME" and name == "@":
            raise HostingError("A CNAME cannot be set on the domain itself (@). Use an A record there.")
    elif kind == "TXT":
        if not value or len(value) > 2000 or any(ord(c) < 32 for c in value):
            raise HostingError("A TXT record needs some text (up to 2000 characters, on one line).")
    elif kind == "SRV":
        parts = value.split()
        if len(parts) != 3 or not all(p.isdigit() and int(p) <= 65535 for p in parts[:2]) or not HOST_RE.match(parts[2].lower().rstrip(".")):
            raise HostingError("An SRV value is three parts: weight, port and host, for example: 5 5060 sip.example.com")
        if not SRV_NAME_RE.match(name):
            raise HostingError("An SRV name looks like _sip._tcp.")
        value = f"{parts[0]} {parts[1]} {parts[2].lower().rstrip('.')}"
    elif kind == "CAA":
        match = CAA_RE.match(value)
        if not match:
            raise HostingError('A CAA value looks like: 0 issue "letsencrypt.org"')
        value = f'{match.group(1)} {match.group(2)} "{match.group(3)}"'
    if kind in ("MX", "SRV"):
        try:
            priority = int(record.priority)
        except (TypeError, ValueError):
            raise HostingError("Give a priority (a number from 0 to 65535).")
        if not 0 <= priority <= 65535:
            raise HostingError("The priority must be between 0 and 65535.")
    return kind, name, value, ttl, priority
