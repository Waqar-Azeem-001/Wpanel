"""
A raw WHOIS lookup (RFC 3912) for a staff tool - not a registrar operation, so it lives beside the registrar
adapters rather than inside one: it asks the public WHOIS system directly, the same way a `whois` command-line
tool would, and needs no provider credentials.

Two queries: ask IANA's own WHOIS server which server is authoritative for the name's TLD, then ask that server
about the domain itself. Single-label TLDs only (the same limitation ``domain_tld`` already has - see its
docstring); a multi-part TLD such as ``.co.uk`` is asked about as if ``uk`` were the whole TLD, which usually
still reaches a server that at least explains the name is not there.
"""
import socket

from .models import domain_tld

IANA_WHOIS = "whois.iana.org"
PORT = 43
TIMEOUT = 10
MAX_BYTES = 200_000  # a WHOIS server that never stops talking must not be read forever


class WhoisError(Exception):
    """The lookup itself could not be completed (no server known, unreachable, timed out)."""


def _query(server, question, *, timeout=TIMEOUT):
    try:
        with socket.create_connection((server, PORT), timeout=timeout) as sock:
            sock.sendall((question.strip() + "\r\n").encode("ascii", "ignore"))
            sock.settimeout(timeout)
            chunks, total = [], 0
            while total < MAX_BYTES:
                data = sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
                total += len(data)
    except OSError as exc:
        raise WhoisError(f"Could not reach {server}: {exc}") from exc
    return b"".join(chunks).decode("utf-8", "replace")


def _referred_server(referral_text):
    for line in referral_text.splitlines():
        if line.lower().startswith("whois:"):
            return line.split(":", 1)[1].strip()
    return None


def lookup(name, *, query=_query):
    """
    The WHOIS record for a domain name, as plain text. ``query`` is swappable so a test never opens a real socket.
    Raises ``WhoisError`` for anything that stops an answer arriving - a bad name, no known server for the TLD, or
    the network itself.
    """
    name = (name or "").strip().lower().rstrip(".")
    if not name or "." not in name:
        raise WhoisError("Enter a full domain name, e.g. example.com.")
    tld = domain_tld(name).lstrip(".")
    try:
        referral = query(IANA_WHOIS, tld)
        server = _referred_server(referral)
        if not server:
            raise WhoisError(f"No WHOIS server is known for .{tld}.")
        return query(server, name)
    except WhoisError:
        raise
    except OSError as exc:
        raise WhoisError(f"Could not complete the lookup: {exc}") from exc
