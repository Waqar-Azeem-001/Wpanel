"""
Logs on a cPanel server. The website's recent errors come from UAPI ``Stats::get_site_errors``. The access log is not offered: cPanel keeps it outside
the account's own folder, so a customer login cannot read it. Clearing a log is not offered either.
"""
from .base import HostingError
from .capabilities import LOG_LINE_CHOICES, LogsCapability, LogView
from .cpanel_api import CpanelApi


def _line(item):
    if isinstance(item, dict):
        return str(item.get("message") or item.get("line") or item.get("error") or "").strip()
    return str(item).strip()


class CpanelLogs(CpanelApi, LogsCapability):
    def read_log(self, username, kind, lines, query):
        if kind == "access":
            self._unsupported("The access log")
        if kind != "error":
            raise HostingError("Choose the error log or the access log.")
        if lines not in LOG_LINE_CHOICES:
            raise HostingError("Choose how many lines to show.")
        main = self._account_domains(username)[0]
        rows = [r for r in (_line(i) for i in self._uapi(username, "Stats", "get_site_errors", {"domain": main}) or []) if r]
        query = (query or "").strip()[:100]
        if query:
            rows = [r for r in rows if query.lower() in r.lower()]
        return LogView(kind="error", lines=rows[-lines:], total=len(rows), size_bytes=sum(len(r) + 1 for r in rows))

    def clear_log(self, username, kind):
        self._unsupported("Clearing a log")
