"""
What a report is: parameters in, a table (and a few headline figures) out.

Every report builds a ``Result``. The screen, the JSON API and the CSV / XLSX / PDF exports are all drawn from that one
object, and every value is formatted in one place (``formatting``), so the same figure never appears two ways.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

MAX_ROWS = 50_000      # a report (and so an export) never holds more than this many rows
SCREEN_ROWS = 200      # the page shows this many; the export has them all
MAX_DAYS = 3660        # the longest date range
MAX_DAILY_DAYS = 731   # a day-by-day report covers at most about two years
GROUPS = (("day", "Day"), ("month", "Month"), ("year", "Year"))
SCOPES = (("all", "Hosting and domains"), ("hosting", "Hosting"), ("domain", "Domains"))


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    kind: str = "text"  # text | int | money | date | datetime | percent | hours


@dataclass
class Result:
    title: str
    columns: list
    rows: list
    summary: list = field(default_factory=list)   # [(label, kind, value)]
    notes: list = field(default_factory=list)
    params: dict = field(default_factory=dict)    # what the report was run with, for display
    truncated: bool = False


@dataclass(frozen=True)
class ReportDef:
    slug: str
    title: str
    group: str
    description: str
    permissions: tuple       # the area permissions needed besides ``view_reports`` (all of them)
    uses: tuple = ()         # which parameters it takes: "range", "group", "days", "scope"
    build: object = None


@dataclass(frozen=True)
class Params:
    start: date
    end: date
    group: str = "day"
    days: int = 30
    scope: str = "all"

    @property
    def start_at(self):
        """The first moment of the range, in the site's time zone."""
        return timezone.make_aware(datetime.combine(self.start, datetime.min.time()))

    @property
    def end_at(self):
        """The moment after the last day of the range (an exclusive bound)."""
        return timezone.make_aware(datetime.combine(self.end + timedelta(days=1), datetime.min.time()))


def _date(value, label):
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise ValidationError(f"{label} must be a date like 2026-09-25.")


def parse_params(definition, data, *, today=None):
    """Read and check the parameters a report takes; anything it does not use is ignored."""
    today = today or timezone.localdate()
    data = data or {}
    end, group, days, scope = today, "day", 30, "all"
    start = end - timedelta(days=29)
    if "range" in definition.uses:
        end = _date(data.get("to"), "The end date") or today
        start = _date(data.get("from"), "The start date") or end - timedelta(days=29)
        if start > end:
            raise ValidationError("The start date must not be after the end date.")
        if (end - start).days + 1 > MAX_DAYS:
            raise ValidationError("Choose a range of at most 10 years.")
    if "group" in definition.uses:
        group = (data.get("group") or "day").strip().lower()
        if group not in dict(GROUPS):
            raise ValidationError("Group by day, month or year.")
        if group == "day" and (end - start).days + 1 > MAX_DAILY_DAYS:
            raise ValidationError("A day-by-day report covers at most two years; group by month or year instead.")
    if "days" in definition.uses:
        try:
            days = int(data.get("days") or 30)
        except (TypeError, ValueError):
            raise ValidationError("Days must be a whole number.")
        if not 1 <= days <= 365:
            raise ValidationError("Days must be between 1 and 365.")
    if "scope" in definition.uses:
        scope = (data.get("scope") or "all").strip().lower()
        if scope not in dict(SCOPES):
            raise ValidationError("Choose hosting, domains or both.")
    return Params(start=start, end=end, group=group, days=days, scope=scope)


def query_for(definition, params):
    """The parameters a report used, as a query string (so a download repeats exactly what is on the screen)."""
    from urllib.parse import urlencode

    pairs = []
    if "range" in definition.uses:
        pairs += [("from", params.start.isoformat()), ("to", params.end.isoformat())]
    if "group" in definition.uses:
        pairs.append(("group", params.group))
    if "days" in definition.uses:
        pairs.append(("days", params.days))
    if "scope" in definition.uses:
        pairs.append(("scope", params.scope))
    return urlencode(pairs)


def describe(definition, params):
    """The parameters a report used, as shown on the page and printed on exports."""
    shown = {}
    if "range" in definition.uses:
        shown["From"], shown["To"] = params.start.isoformat(), params.end.isoformat()
    if "group" in definition.uses:
        shown["Grouped by"] = dict(GROUPS)[params.group]
    if "days" in definition.uses:
        shown["Within"] = f"{params.days} days"
    if "scope" in definition.uses:
        shown["Services"] = dict(SCOPES)[params.scope]
    return shown

