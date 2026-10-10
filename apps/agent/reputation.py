"""
Mail reputation monitoring (N7). A mail node's address is the most valuable thing the mail service owns: once it is on a blacklist, customers' mail stops arriving.
This asks the public blacklists about each mail node's addresses and keeps the answer next to the queue size the node reports in its heartbeat. Run it from the
``check_mail_reputation`` command (for example every hour). Blacklist answers depend on the resolver being allowed to ask: a refused or failed query is reported as a
problem with the CHECK, never as "clean".
"""
import ipaddress

import dns.exception
import dns.resolver
from django.utils import timezone

from apps.audit import services as audit

from .models import AgentNode, NodeStatus

BLACKLISTS = ("zen.spamhaus.org", "b.barracudacentral.org", "bl.spamcop.net", "dnsbl.sorbs.net")
QUEUE_WARN = 200  # messages waiting in the mail queue


def _resolver_lookup(name):
    try:
        return [r.to_text() for r in dns.resolver.resolve(name, "A", lifetime=4)]
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []


def check_ip(ip, lookup=None):
    """({listed zones}, {zones that could not be checked}) for one IPv4 address."""
    lookup = lookup or _resolver_lookup
    if ipaddress.ip_address(ip).version != 4:
        return set(), set()
    reverse = ".".join(reversed(ip.split(".")))
    listed, failed = set(), set()
    for zone in BLACKLISTS:
        try:
            answers = lookup(f"{reverse}.{zone}")
        except (dns.exception.DNSException, OSError):
            failed.add(zone)
            continue
        if any(a.startswith("127.255.255.") for a in answers):  # the list refuses open resolvers: that is not "listed"
            failed.add(zone)
        elif answers:
            listed.add(zone)
    return listed, failed


def check_node(node, lookup=None, record=True):
    """Check every IPv4 address of a mail node and save the result on it. Returns the result dict."""
    ips = sorted({i for i in node.ip_addresses if ":" not in i} | ({node.server.ip_address} if node.server.ip_address and ":" not in node.server.ip_address else set()))
    listed, failed = {}, set()
    for ip in ips:
        l, f = check_ip(ip, lookup)
        if l:
            listed[ip] = sorted(l)
        failed |= f
    queue = (node.health or {}).get("mail_queue")
    result = {"checked_at": timezone.now().isoformat(), "addresses": ips, "listed": listed, "unchecked": sorted(failed), "queue": queue,
              "queue_high": queue is not None and queue > QUEUE_WARN, "ok": not listed and not (queue is not None and queue > QUEUE_WARN)}
    if record:
        was_ok = (node.reputation or {}).get("ok", True)
        node.reputation, node.reputation_at = result, timezone.now()
        node.save(update_fields=["reputation", "reputation_at", "updated_at"])
        if not result["ok"] and was_ok:  # tell the audit trail once, when it turns bad
            audit.record("agent.mail_reputation_bad", target=node, metadata={"listed": listed, "queue": queue})
    return result


def check_all(lookup=None):
    """Every enrolled mail node. Returns {server name: result}."""
    out = {}
    for node in AgentNode.objects.select_related("server").filter(status=NodeStatus.ENROLLED):
        if "mail" in node.capabilities:
            out[node.server.name] = check_node(node, lookup)
    return out
