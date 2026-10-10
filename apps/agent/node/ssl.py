"""
What the node does for certificates (N5): get one over HTTP-01, install it into the account's web server config, read its status, switch renewal and the HTTPS
redirect, remove it, and renew those that are close to expiry (``renew_due``, run by the agent daily). Certificate and key files belong to root, and the key is
readable by root only: the account's own user can never read it (ADR 08).
"""
import datetime
import json

from cryptography import x509

from .. import catalog
from . import render
from .acme import AcmeError
from .system import SystemError_
from .website import HandlerError, Websites

RENEW_WITHIN_DAYS = 30
EXPIRING_DAYS = 14


def tls_paths(account, domain):
    base = f"/etc/whp/tls/{account}/{domain}"
    return {"dir": base, "chain": base + "/fullchain.pem", "key": base + "/privkey.pem"}


class Ssl:
    def __init__(self, system, acme, now=None):
        self.s, self.acme, self.web = system, acme, Websites(system)
        self.now = now or (lambda: datetime.datetime.now(datetime.timezone.utc))

    def _site(self, account, domain):
        state = self.web._need(account, domain)
        return state

    def _read_cert(self, account, domain):
        pem = self.s.read(tls_paths(account, domain)["chain"])
        return x509.load_pem_x509_certificate(pem.encode()) if pem else None

    def _write_vhost(self, account, state):
        ssl = state.get("ssl")
        self.s.write(render.paths(account, state["domain"])["vhost"], render.vhost(account, state["domain"], suspended=state["suspended"], ssl=ssl))
        self.web._reload_web()

    def _status_row(self, account, state):
        cert = self._read_cert(account, state["domain"])
        if cert is None:
            return {"domain": state["domain"], "status": "none", "issuer": "", "issued_at": None, "expires_at": None, "days_left": None,
                    "auto_renew": False, "https_redirect": False}
        left = (cert.not_valid_after_utc - self.now()).days
        ssl = state.get("ssl") or {}
        return {"domain": state["domain"], "status": "expired" if left < 0 else "expiring" if left <= EXPIRING_DAYS else "active",
                "issuer": cert.issuer.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value, "issued_at": cert.not_valid_before_utc.isoformat(),
                "expires_at": cert.not_valid_after_utc.isoformat(), "days_left": left, "auto_renew": ssl.get("auto_renew", True),
                "https_redirect": ssl.get("https_redirect", False)}

    # --- commands ---------------------------------------------------------------------------------------------------------------------
    def run(self, command, params):
        try:
            p = catalog.validate(command, params)
            handler = {"ISSUE_SSL": self.issue, "REMOVE_SSL": self.remove, "SSL_STATUS": self.status, "SET_SSL_OPTIONS": self.options}[command]
            return handler(p)
        except catalog.InvalidCommand as exc:
            raise HandlerError("invalid", str(exc)) from None
        except AcmeError as exc:
            raise HandlerError(exc.code, exc.message) from None
        except SystemError_ as exc:
            raise HandlerError("system_error", str(exc)[:300]) from None

    def issue(self, p):
        account, domain = p["account"], p["domain"]
        state = self._site(account, domain)
        if state["suspended"]:
            raise HandlerError("suspended", "This website is suspended.")
        paths, old = tls_paths(account, domain), state.get("ssl")
        chain, key = self.acme.obtain([domain, "www." + domain], render.paths(account, domain)["docroot"], p["method"])
        self.s.mkdir(paths["dir"], 0o700)
        self.s.write(paths["key"], key, 0o600)  # root only
        self.s.write(paths["chain"], chain, 0o644)
        state["ssl"] = old or {"auto_renew": True, "https_redirect": False}
        try:
            self._write_vhost(account, state)
        except Exception:
            state["ssl"] = old  # the web server refused the new config: put back what worked
            if old is None:
                self.s.remove(paths["dir"])
            self.s.write(render.paths(account, domain)["vhost"], render.vhost(account, domain, suspended=False, ssl=old))
            raise
        self.web._save(account, state)
        return "issued", self._status_row(account, state)

    def remove(self, p):
        account, domain = p["account"], p["domain"]
        state = self._site(account, domain)
        if not state.get("ssl") and not self.s.exists(tls_paths(account, domain)["chain"]):
            return "absent", {}
        state["ssl"] = None
        self._write_vhost(account, state)
        self.s.remove(tls_paths(account, domain)["dir"])
        self.web._save(account, state)
        return "removed", {}

    def status(self, p):
        state = self.web._state(p["account"])
        return "listed", {"certificates": [self._status_row(p["account"], state)] if state else []}

    def options(self, p):
        account, domain = p["account"], p["domain"]
        state = self._site(account, domain)
        if not state.get("ssl"):
            raise HandlerError("no_certificate", "There is no certificate for this domain yet.")
        state["ssl"] = {"auto_renew": p["auto_renew"] == "yes", "https_redirect": p["https_redirect"] == "yes"}
        self._write_vhost(account, state)
        self.web._save(account, state)
        return "changed", self._status_row(account, state)

    # --- the daily job (not a command: WHPanel does not trigger it) -----------------------------------------------------------------------
    def renew_due(self, accounts):
        """Renew every certificate with 30 days or less left whose owner has not switched renewal off. Returns [(account, domain, "renewed"|reason)]."""
        done, reload_needed = [], False
        for account in accounts:
            state = self.web._state(account)
            if not state or not state.get("ssl") or not state["ssl"].get("auto_renew", True) or state["suspended"]:
                continue
            row = self._status_row(account, state)
            if row["days_left"] is None or row["days_left"] > RENEW_WITHIN_DAYS:
                continue
            try:
                chain, key = self.acme.obtain([state["domain"], "www." + state["domain"]], render.paths(account, state["domain"])["docroot"], "http-01")
            except AcmeError as exc:  # one failure must not stop the others; it is tried again tomorrow
                done.append((account, state["domain"], exc.code))
                continue
            paths = tls_paths(account, state["domain"])
            self.s.write(paths["key"], key, 0o600)
            self.s.write(paths["chain"], chain, 0o644)
            reload_needed = True
            done.append((account, state["domain"], "renewed"))
        if reload_needed:
            self.web._reload_web()  # one reload for all
        return done
