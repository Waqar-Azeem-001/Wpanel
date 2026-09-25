"""
"With selected..." actions on staff lists (roadmap Section 11.4).

A bulk action is only a loop over the ordinary service call for each selected record: the same permission checks, rules and
audit trail as doing it one by one. One record failing never stops the others; the outcome says how many were done and why
the rest were not. Nothing is written here that a service does not write.
"""
from django.contrib import messages
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from apps.core.exceptions import ServiceError

MAX_SELECTED = 200


def selected_ids(request, name="ids"):
    """The ticked records: whole numbers only, no repeats, at most ``MAX_SELECTED``."""
    found = []
    for raw in request.POST.getlist(name):
        if raw.isdigit() and int(raw) not in found:
            found.append(int(raw))
    return found[:MAX_SELECTED]


def back_to(request, default):
    """Where to return: the list as it was filtered (``next``), but only ever a page on this site."""
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        return redirect(target)
    return redirect(default)


def run(request, queryset, ids, action, *, verb, label):
    """
    Apply ``action(record)`` to each selected record that ``queryset`` allows the person to reach.

    Returns ``(done, failures)``, where failures is ``[(label(record), reason)]``, and tells the person the outcome.
    Ids that match nothing are ignored (never an error), so a stale page cannot act on records it never showed.
    """
    records = list(queryset.filter(pk__in=ids)) if ids else []
    done, failures = 0, []
    for record in records:
        try:
            action(record)
        except ServiceError as exc:
            failures.append((label(record), exc.message))
        except Exception as exc:  # noqa: BLE001 - a validation error on one record must not stop the rest
            failures.append((label(record), "; ".join(getattr(exc, "messages", None) or [type(exc).__name__])))
        else:
            done += 1
    if not ids:
        messages.warning(request, "Tick at least one row first.")
    else:
        if done:
            messages.success(request, f"{done} {verb}.")
        if failures:
            shown = "; ".join(f"{name}: {reason}" for name, reason in failures[:5])
            more = f" (and {len(failures) - 5} more)" if len(failures) > 5 else ""
            messages.error(request, f"{len(failures)} could not be changed. {shown}{more}")
        if not records:
            messages.warning(request, "Nothing you selected could be found.")
    return done, failures
