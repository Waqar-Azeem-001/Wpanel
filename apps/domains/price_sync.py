"""
Keep the domain (TLD) price list in step with the public price list on the company's own website, so prices are not typed in again every time they change there.

The source is one web page with a price table (TLD, minimum years, register, transfer, renew; ``settings.TLD_PRICE_SOURCE_URL``). The page is read, every row is checked, and only then
are prices written, through the same service the staff screen uses (so every change is audited). Nothing is ever deleted or switched off here: an extension that is not on the page is
left alone, and the registrar's maximum years and redemption price are kept.

Safety rules, because a wrong price is a real financial loss:
* the page must be on https and on a public address, and must be small;
* the currency written on the page must be the store's currency, otherwise nothing is changed;
* fewer than ``MIN_ROWS`` valid rows means the page changed shape: nothing is changed;
* a price that moves by more than ``MAX_CHANGE`` times (up or down) is held back and reported unless ``force`` is given.
"""
import ipaddress
import re
import socket
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests
from django.conf import settings

from apps.audit import services as audit
from apps.core.exceptions import ServiceError
from apps.core.system import SYSTEM

from . import services
from .models import TldPricing

MIN_ROWS = 10
MAX_CHANGE = Decimal("3")
MAX_BYTES = 3_000_000
TLD_RE = re.compile(r"^\.[a-z0-9]{2,24}(\.[a-z0-9]{2,12})?$")
PRICE_RE = re.compile(r"^([A-Za-z]{3})?\s*([0-9][0-9,]*(?:\.[0-9]+)?)$")


def source_url():
    return getattr(settings, "TLD_PRICE_SOURCE_URL", "") or "https" + "://webhostera.pk/domain-pricing/"


class _TableReader(HTMLParser):
    """Collects every table of the page as rows of cell texts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._table, self._row, self._cell = [], None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = {"id": dict(attrs).get("id", ""), "class": dict(attrs).get("class", ""), "rows": []}
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table["rows"].append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse(html):
    """The price rows of the page: ([{"tld", "min_years", "register", "transfer", "renew"}], currency or ""). Rows that do not look right are left out."""
    reader = _TableReader()
    reader.feed(html)
    columns = ("tld", "min years", "register", "transfer", "renew")
    chosen = None
    for table in reader.tables:
        header = [c.lower().strip(" .") for c in (table["rows"][0] if table["rows"] else [])]
        if all(any(want in cell for cell in header) for want in ("tld", "register", "renew", "transfer")):
            chosen = table
            if table["id"] == "tablepress-domains":
                break
    if chosen is None:
        return [], ""
    header = [c.lower().strip(" .") for c in chosen["rows"][0]]

    def column(word):
        return next((i for i, cell in enumerate(header) if word in cell), None)

    at = {name: column(name if name != "min years" else "min") for name in columns}
    if any(v is None for k, v in at.items() if k != "min years"):
        return [], ""
    rows, currency = [], ""
    for cells in chosen["rows"][1:]:
        try:
            tld = cells[at["tld"]].strip().lower()
            prices, codes = {}, set()
            for key in ("register", "transfer", "renew"):
                match = PRICE_RE.match(cells[at[key]].strip())
                if match is None:
                    raise ValueError(key)
                if match.group(1):
                    codes.add(match.group(1).upper())
                prices[key] = Decimal(match.group(2).replace(",", ""))
            years = int(cells[at["min years"]]) if at["min years"] is not None and cells[at["min years"]].strip().isdigit() else 1
        except (IndexError, ValueError, InvalidOperation):
            continue
        if not TLD_RE.match(tld) or any(p <= 0 or p > Decimal("10000000") for p in prices.values()) or not 1 <= years <= 10 or len(codes) > 1:
            continue
        currency = currency or (codes.pop() if codes else "")
        rows.append({"tld": tld, "min_years": years, **prices})
    return rows, currency


def _public(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return bool(infos)


def fetch(url=None, hops=3):
    """The page text. Redirects (the site moves its pages) are followed a few times, each one checked again: https and a public address only."""
    from urllib.parse import urljoin

    current = url or source_url()
    for _ in range(hops + 1):
        parts = urlparse(current)
        if parts.scheme != "https" or not parts.hostname or not _public(parts.hostname):
            raise ServiceError("The price list address must be a public https address.", code="bad_source")
        try:
            reply = requests.get(current, headers={"User-Agent": "WHPanel-PriceSync/1.0"}, timeout=(6, 25), stream=True, allow_redirects=False)
        except requests.exceptions.RequestException as exc:
            raise ServiceError("The price list could not be read (" + type(exc).__name__ + ").", code="unreachable") from exc
        try:
            if reply.is_redirect and reply.headers.get("Location"):
                current = urljoin(current, reply.headers["Location"])
                continue
            if reply.status_code != 200:
                raise ServiceError(f"The price list answered with status {reply.status_code}.", code="bad_status")
            body = reply.raw.read(MAX_BYTES + 1, decode_content=True)
        finally:
            reply.close()
        if len(body) > MAX_BYTES:
            raise ServiceError("The price list page is larger than expected, so it was not used.", code="too_large")
        return body.decode("utf-8", "replace")
    raise ServiceError("The price list address redirected too many times.", code="too_many_redirects")


def sync(*, html=None, url=None, dry_run=False, force=False, actor=SYSTEM):
    """Read the price list and apply it. Returns a report; raises ServiceError when nothing could be trusted."""
    page = html if html is not None else fetch(url)
    rows, currency = parse(page)
    from apps.branding.services import store_currency

    store = store_currency()
    if len(rows) < MIN_ROWS:
        raise ServiceError(f"The price list page did not look as expected ({len(rows)} usable rows, at least {MIN_ROWS} needed), so no price was changed.", code="unexpected_page")
    if currency and store and currency != store:
        raise ServiceError(f"The price list is in {currency} but the store's currency is {store}. No price was changed: choose {currency} as the Store currency in Setup > Branding first.", code="currency_mismatch")
    existing = {p.tld: p for p in TldPricing.objects.all()}
    report = {"source": url or source_url(), "rows": len(rows), "created": [], "updated": [], "unchanged": 0, "held": [], "dry_run": dry_run}
    for row in rows:
        current = existing.get(row["tld"])
        if current is not None:
            same = (current.register_price == row["register"] and current.renew_price == row["renew"] and current.transfer_price == row["transfer"]
                    and current.min_years == row["min_years"])
            if same:
                report["unchanged"] += 1
                continue
            jumps = [new / old for old, new in ((current.register_price, row["register"]), (current.renew_price, row["renew"]), (current.transfer_price, row["transfer"])) if old]
            if not force and any(j > MAX_CHANGE or j < 1 / MAX_CHANGE for j in jumps):
                report["held"].append({"tld": row["tld"], "old": str(current.register_price), "new": str(row["register"])})
                continue
            report["updated"].append({"tld": row["tld"], "old": str(current.register_price), "new": str(row["register"])})
        else:
            report["created"].append({"tld": row["tld"], "new": str(row["register"])})
        if not dry_run:
            services.set_tld_pricing(actor, row["tld"], register_price=row["register"], renew_price=row["renew"], transfer_price=row["transfer"],
                                     redemption_price=current.redemption_price if current else 0, min_years=row["min_years"],
                                     max_years=max(current.max_years if current else 10, row["min_years"]))
    if not dry_run:
        audit.record("tld_pricing.synced", metadata={"rows": report["rows"], "created": len(report["created"]), "updated": len(report["updated"]),
                                                      "held": len(report["held"]), "source": report["source"], "forced": force})
    return report


def run_and_record(**kwargs):
    """``sync`` for the scheduled job and the staff button: a failure is written to the audit trail (so the screen can show it) and returned, never raised."""
    try:
        report = sync(**kwargs)
        return {"ok": True, **report}
    except ServiceError as exc:
        audit.record("tld_pricing.sync_failed", metadata={"error": exc.message, "code": exc.code})
        return {"ok": False, "error": exc.message}


def last_run():
    """The most recent sync (or failure) as audit shows it, for the staff screen: {"when", "ok", "summary"} or None."""
    from apps.audit.models import AuditEvent

    event = AuditEvent.objects.filter(action__in=["tld_pricing.synced", "tld_pricing.sync_failed"]).order_by("-created_at").first()
    if event is None:
        return None
    meta = event.metadata or {}
    if event.action == "tld_pricing.sync_failed":
        return {"when": event.created_at, "ok": False, "summary": meta.get("error", "The sync failed.")}
    text = f"{meta.get('created', 0)} added, {meta.get('updated', 0)} updated"
    if meta.get("held"):
        text += f", {meta['held']} held back for review (price moved more than 3 times)"
    return {"when": event.created_at, "ok": True, "summary": text}
