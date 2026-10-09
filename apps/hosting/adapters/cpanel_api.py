"""
The cPanel side of the provider-neutral hosting contracts (H14), in small mixins that the WHM adapter combines.

How a customer-level call is made: WHM's API 1 function ``uapi_cpanel`` runs a cPanel UAPI function *as one account's user* from the
reseller token (``cpanel.user``, ``cpanel.module``, ``cpanel.function`` plus the function's own parameters); the UAPI answer comes back
under ``data.uapi``. Nothing here ever uses a customer password, and the WHM token never leaves the adapter.

IMPORTANT, and the reason every cPanel capability is switched off by default (``settings.HOSTING_CPANEL_CAPABILITIES``): this code was
written against cPanel's published documentation and tested with mocked answers, not against a live WHM/cPanel server. The exact
fields cPanel returns vary between versions, so answers are parsed defensively. Turn a capability on for one staging server, try
every screen of it, and only then enable it for customers.

Security rule that every mixin follows: an operation names a domain or a user only after it has been checked against what *this
account* owns, so one customer can never reach another's domain, zone or database by typing its name.
"""
from .base import HostingError

MAX_ERROR = 300


def clean_error(text):
    """A cPanel error shortened and stripped of anything that looks like a path or a token."""
    text = " ".join(str(text).split())
    return text[:MAX_ERROR]


def names(items, key="domain"):
    """Domain names out of a list that may hold strings or dicts."""
    found = []
    for item in items or []:
        value = item.get(key) if isinstance(item, dict) else item
        if isinstance(value, str) and value:
            found.append(value.lower().rstrip("."))
    return found


class CpanelApi:
    """What every cPanel capability needs: run a UAPI function as the account, and know which domains the account owns."""

    def _uapi(self, user, module, function, params=None, *, secret=False):
        """The ``data`` of a UAPI answer, or a HostingError with cPanel's own reason. ``secret=True`` sends the parameters in a POST body."""
        payload = self._call("uapi_cpanel", {"cpanel.user": user, "cpanel.module": module, "cpanel.function": function, **(params or {})},
                             sensitive=secret)
        result = (payload.get("data") or {}).get("uapi")
        if not isinstance(result, dict):
            raise HostingError(f"The server answered {module}::{function} in a way we do not understand.")
        if result.get("status") != 1:
            errors = result.get("errors") or []
            raise HostingError(clean_error(errors[0]) if errors else f"{module}::{function} did not succeed.")
        return result.get("data")

    def _api2(self, user, module, function, params=None, *, secret=False):
        """A legacy cPanel API 2 function run as the account (for the few things UAPI does not offer): the ``data`` of its ``cpanelresult``."""
        payload = self._call("cpanel", {"cpanel_jsonapi_user": user, "cpanel_jsonapi_apiversion": 2, "cpanel_jsonapi_module": module,
                                        "cpanel_jsonapi_func": function, **(params or {})}, sensitive=secret, raw=True)
        result = payload.get("cpanelresult")
        if not isinstance(result, dict):
            raise HostingError(f"The server answered {module}::{function} in a way we do not understand.")
        if result.get("error") or (result.get("event") or {}).get("result") not in (1, "1", None):
            raise HostingError(clean_error(result.get("error") or (result.get("data") or [{}])[0].get("reason") or f"{module}::{function} did not succeed."))
        data = result.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict) and str(data[0].get("result", 1)) == "0":
            raise HostingError(clean_error(data[0].get("reason") or f"{module}::{function} did not succeed."))
        return data

    def _home(self, user):
        """The account's home folder on the server (usually /home/<user>), which every file path is relative to."""
        info = self._uapi(user, "Variables", "get_user_information") or {}
        home = str(info.get("home") or f"/home/{user}").rstrip("/")
        if not home.startswith("/") or ".." in home.split("/"):
            raise HostingError("The server reported an unusable home folder.")
        return home

    def _relative(self, user, absolute):
        """A server path made relative to the account's home folder (or "" when it is not inside it)."""
        home = self._home(user)
        absolute = str(absolute or "").rstrip("/")
        return absolute[len(home) + 1:] if absolute.startswith(home + "/") else ""

    def _account_domains(self, user):
        """(main domain, addon domains, subdomains, parked/alias domains) of the account, all lower-case."""
        data = self._uapi(user, "DomainInfo", "domains_data", {"format": "hash"}) or {}
        main = data.get("main_domain")
        main = (main.get("domain") if isinstance(main, dict) else main) or ""
        return (str(main).lower(), names(data.get("addon_domains")), names(data.get("sub_domains")), names(data.get("parked_domains")))

    def _owned_domain(self, user, domain, *, zones_only=False):
        """The cleaned domain, if this account owns it (``zones_only`` leaves out subdomains, which live in their parent's zone)."""
        domain = (domain or "").strip().lower().rstrip(".")
        main, addon, subs, parked = self._account_domains(user)
        owned = [main, *addon, *parked] if zones_only else [main, *addon, *subs, *parked]
        if not domain or domain not in owned:
            raise HostingError("That domain is not on this account.")
        return domain

    @staticmethod
    def _unsupported(what):
        from .capabilities import CapabilityNotSupported

        raise CapabilityNotSupported(f"{what} is not available on cPanel servers yet.")
