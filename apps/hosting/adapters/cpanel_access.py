"""
TRANSITIONAL (H15): the one-time sign-in into cPanel, through WHM's ``create_user_session``.

WHM makes a short-lived, single-use link for one account's cPanel; the customer's browser is sent there. Nothing is typed or shown:
no cPanel password is involved, the WHM token is only used here on the server side, and the link is returned to the caller to be used
once and is never written to a log, the audit trail or a page. Remove this file with cPanel (see docs/h15-cpanel-escape-hatch.md).
"""
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .base import HostingError
from .capabilities import CpanelAccessCapability, CpanelAccessSession
from .cpanel_api import CpanelApi


class CpanelAccess(CpanelApi, CpanelAccessCapability):
    def open_cpanel_access(self, username):
        data = (self._call("create_user_session", {"user": username, "service": "cpaneld"}).get("data") or {})
        url = str(data.get("url") or "")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise HostingError("The server did not give a usable sign-in link.")
        try:
            expires = datetime.fromtimestamp(int(data.get("expires")), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            expires = None
        return CpanelAccessSession(url=url, expires_at=expires)
