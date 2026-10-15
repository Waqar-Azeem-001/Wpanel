"""
Where a website's domain stands, checked when the website is set up and shown on its dashboard:

* ``ours``: the domain is registered (or transferred in) with us for this client, so DNS and renewals can be managed in one place;
* ``points_here``: the domain's address already leads to this hosting's server.

A domain that is not with us is not an error: the website is still installed, the customer is invited to transfer the domain to us or point it here, and meanwhile can preview the
website with a one-line change on their own computer (the hosts file), which shows exactly what visitors will see once DNS points here.
"""
import socket

from apps.domains.models import Domain, DomainStatus


def _addresses(name):
    try:
        return {info[4][0] for info in socket.getaddrinfo(name, None, socket.AF_INET)}
    except OSError:
        return set()


def check(account, domain=""):
    name = (domain or account.domain).lower()
    rows = Domain.objects.filter(client_id=account.client_id, name__iexact=name, status__in=[DomainStatus.ACTIVE, DomainStatus.PENDING_TRANSFER_IN])
    ours = rows.filter(status=DomainStatus.ACTIVE).exists()
    transferring = rows.filter(status=DomainStatus.PENDING_TRANSFER_IN).exists()
    ip = str(account.server.ip_address) if account.server is not None and account.server.ip_address else ""
    resolved = _addresses(name)
    points_here = bool(ip) and ip in resolved
    if transferring:
        state = "transferring"
    elif ours and points_here:
        state = "ready"
    elif ours:
        state = "ours_not_pointing"
    elif points_here:
        state = "pointed_elsewhere_registrar"  # lives here already, registered elsewhere
    else:
        state = "not_ours"
    return {"domain": name, "ours": ours, "points_here": points_here, "ip": ip, "resolved": sorted(resolved), "state": state, "transferring": transferring,
            "hosts_line": f"{ip} {name} www.{name}" if ip else "", "preview_url": "https" + "://" + name + "/"}
