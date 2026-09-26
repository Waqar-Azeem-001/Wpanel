"""
Why a message the mail server "accepted" may still not reach the inbox: the checks a receiving server (Gmail, Outlook) makes.

The SMTP server saying OK only means it took the message. Receivers then check that the *sending server is allowed to send for the
sender's domain* (SPF, from the DNS record of the envelope sender) and that the message is signed (DKIM). A server that is not listed
in the domain's SPF record is treated as a forger: the mail goes to Spam or is dropped without any error here. These checks read
public DNS records only.
"""
import ipaddress
import socket
from email.utils import parseaddr

MAX_DEPTH = 5


class LookupFailed(Exception):
    """DNS could not be reached at all (blocked, offline): different from "the record does not exist"."""


def _query(name, rtype):
    """
    The answers for ``name`` as strings ([] when the record does not exist). Tries the system resolver, then DNS-over-HTTPS (some
    networks block DNS on port 53). Raises ``LookupFailed`` when neither can be reached.
    """
    import dns.exception
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 4
    try:
        answers = resolver.resolve(name, rtype)
        if rtype == "TXT":
            return ["".join(p.decode() if isinstance(p, bytes) else p for p in r.strings) for r in answers]
        if rtype == "MX":
            return [str(r.exchange).rstrip(".") for r in answers]
        return [r.address for r in answers]
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except (dns.exception.Timeout, dns.resolver.NoNameservers, OSError):
        pass
    import requests
    from django.conf import settings

    try:
        data = requests.get(settings.DNS_OVER_HTTPS_URL, params={"name": name, "type": rtype}, timeout=6).json()
    except (requests.RequestException, ValueError):
        raise LookupFailed(name)
    found = [a["data"] for a in data.get("Answer", []) if a.get("type") == {"TXT": 16, "MX": 15, "A": 1, "AAAA": 28}[rtype]]
    if rtype == "TXT":
        return [x.strip('"').replace('" "', "") for x in found]
    if rtype == "MX":
        return [x.split()[-1].rstrip(".") for x in found]
    return found


def _txt(domain):
    return _query(domain, "TXT")


def _addresses(domain):
    found = []
    for kind in ("A", "AAAA"):
        try:
            found += [ipaddress.ip_address(a) for a in _query(domain, kind)]
        except (LookupFailed, ValueError):
            pass
    return found


def _mx_hosts(domain):
    try:
        return _query(domain, "MX")
    except LookupFailed:
        return []


RESULTS = {"+": "pass", "-": "fail", "~": "softfail", "?": "neutral"}


def spf_result(domain, ip, depth=0):
    """
    Would ``ip`` be allowed to send for ``domain`` by its SPF record? "pass", "fail", "softfail", "neutral" or "none".
    Understands ip4, ip6, a, mx, include and redirect (enough for ordinary records).
    """
    record = next((t for t in _txt(domain) if t.lower().startswith("v=spf1")), None)  # may raise LookupFailed
    if record is None:
        return "none"
    ip = ipaddress.ip_address(ip)
    redirect = ""
    for term in record.split()[1:]:
        qualifier = "+"
        if term[0] in RESULTS:
            qualifier, term = term[0], term[1:]
        name, _, arg = term.replace("=", ":", 1).partition(":") if term.startswith("redirect=") else term.partition(":")
        name = name.lower()
        matched = False
        if name in ("ip4", "ip6"):
            try:
                matched = ip in ipaddress.ip_network(arg, strict=False)
            except ValueError:
                matched = False
        elif name == "a" or name.startswith("a/"):
            matched = ip in _addresses(arg or domain)
        elif name == "mx" or name.startswith("mx/"):
            matched = any(ip in _addresses(host) for host in _mx_hosts(arg or domain))
        elif name == "include" and depth < MAX_DEPTH:
            matched = spf_result(arg, ip, depth + 1) == "pass"
        elif name == "all":
            matched = True
        elif name == "redirect":
            redirect = arg
        if matched:
            return RESULTS[qualifier]
    if redirect and depth < MAX_DEPTH:
        return spf_result(redirect, ip, depth + 1)
    return "neutral"


def _domain(address):
    return parseaddr(address or "")[1].rpartition("@")[2].lower()


def report(provider):
    """
    A list of ``(level, text)`` (level: "ok", "warn" or "bad") about mail sent through ``provider``: whether its server is allowed by
    the sender domain's SPF record, whether the From domain matches the login, and whether DKIM and DMARC records exist.
    """
    from_domain = _domain(provider.from_email)
    envelope_domain = _domain(provider.envelope_from) or from_domain
    login_domain = provider.username.rpartition("@")[2].lower() if "@" in provider.username else ""
    checks = []
    try:
        server_ip = socket.gethostbyname(provider.host)
    except OSError:
        return [("bad", f"The mail server name {provider.host} does not resolve, so nothing can be sent.")]
    checks.append(("ok", f"{provider.host} is {server_ip}."))

    if login_domain and login_domain != from_domain and not provider.envelope_from:
        checks.append(("warn", f"You log in as @{login_domain} but send From @{from_domain}. The receiving server checks SPF for "
                               f"{from_domain}. If that record does not list this server, set \"Envelope sender\" to your SMTP "
                               f"login ({provider.username}) or send From an address on {login_domain}."))

    try:
        result = spf_result(envelope_domain, server_ip)
    except LookupFailed:
        return checks + [("warn", "DNS could not be reached from this server, so SPF, DKIM and DMARC could not be checked. Check them "
                                  "with your DNS host or an online SPF checker.")]
    if result == "pass":
        checks.append(("ok", f"SPF: {envelope_domain} allows {server_ip} to send. Good."))
    elif result == "none":
        checks.append(("warn", f"SPF: {envelope_domain} has no SPF record. Add one that lists {server_ip}, or many receivers will "
                               "distrust the mail."))
    else:
        checks.append(("bad", f"SPF: {envelope_domain} does NOT list {server_ip} ({result}). Gmail and others will send this mail "
                              f"to Spam or drop it, even though this server says \"sent\". Add ip4:{server_ip} to the SPF TXT record "
                              f"of {envelope_domain} in your DNS, or set \"Envelope sender\" to an address on a domain that lists it."))

    def has(name):
        try:
            return bool(_txt(name))
        except LookupFailed:
            return False

    dkim = [d for d in (f"default._domainkey.{from_domain}", f"mail._domainkey.{from_domain}", f"x._domainkey.{from_domain}") if has(d)]
    if dkim:
        checks.append(("ok", f"DKIM: a signing key is published for {from_domain} ({dkim[0].split('.')[0]}). Make sure this mail server signs with it."))
    else:
        checks.append(("warn", f"DKIM: no common DKIM key found for {from_domain}. Ask your host to enable DKIM signing for it."))

    try:
        dmarc = next((t for t in _txt(f"_dmarc.{from_domain}") if t.lower().startswith("v=dmarc1")), None)
    except LookupFailed:
        dmarc = None
    if dmarc:
        checks.append(("ok", f"DMARC: {dmarc[:90]}"))
    else:
        checks.append(("warn", f"DMARC: {from_domain} publishes no DMARC record. Gmail and Yahoo expect one from senders."))
    return checks
