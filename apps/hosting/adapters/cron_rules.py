"""
The rules for scheduled jobs, shared by every provider that offers them (the demo provider and the cPanel adapter): only ``php`` followed by a
script inside the account and plain arguments, a valid five-field schedule, nothing more often than every five minutes, ten jobs per account.
"""
import re

from .base import HostingError

MAX_JOBS = 10
MIN_INTERVAL = 5
PRESETS = [("Every 5 minutes", "*/5 * * * *"), ("Every 15 minutes", "*/15 * * * *"), ("Every hour", "0 * * * *"),
           ("Every day at 02:00", "0 2 * * *"), ("Every Sunday at 03:00", "0 3 * * 0"), ("First day of the month", "0 4 1 * *")]
FIELDS = [("minute", 0, 59), ("hour", 0, 23), ("day of the month", 1, 31), ("month", 1, 12), ("day of the week", 0, 7)]
SCRIPT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*\.php$")
ARGUMENT = re.compile(r"^[A-Za-z0-9_.=:/-]{1,100}$")
MAX_COMMAND = 300


def _values(text, low, high, label):
    """The numbers one cron field stands for (supports *, lists, ranges and steps)."""
    found = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                raise HostingError(f"The {label} part of the schedule is not valid.")
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif re.fullmatch(r"\d+-\d+", part):
            start, end = (int(x) for x in part.split("-"))
        elif part.isdigit():
            start = int(part)
            end = high if step > 1 else start
        else:
            raise HostingError(f"The {label} part of the schedule is not valid.")
        if start < low or end > high or start > end:
            raise HostingError(f"The {label} part of the schedule must be between {low} and {high}.")
        found.update(range(start, end + 1, step))
    return found


def check_schedule(schedule):
    parts = (schedule or "").split()
    if len(parts) != 5:
        raise HostingError("A schedule has five parts: minute, hour, day of the month, month and day of the week. "
                           "Pick a common schedule or write them like 0 2 * * *.")
    sets = [_values(p, low, high, label) for p, (label, low, high) in zip(parts, FIELDS)]
    minutes = sorted(sets[0])
    gaps = [b - a for a, b in zip(minutes, minutes[1:])] + [60 - minutes[-1] + minutes[0]]
    if len(minutes) > 1 and min(gaps) < MIN_INTERVAL:
        raise HostingError(f"A job may not run more often than every {MIN_INTERVAL} minutes.")
    return " ".join(parts)


def check_command(command):
    command = " ".join((command or "").split())
    if not command or len(command) > MAX_COMMAND:
        raise HostingError("Enter the PHP script to run, for example: php public_html/cron.php")
    words = command.split(" ")
    if words[0] != "php" or len(words) < 2:
        raise HostingError("Only PHP scripts can be scheduled here. Start the command with php, for example: php public_html/cron.php")
    script = words[1]
    if script.startswith("/") or ".." in script.split("/") or not SCRIPT.match(script):
        raise HostingError("The script must be a .php file inside your hosting folder, written without a leading slash or '..'.")
    if any(not ARGUMENT.match(a) for a in words[2:]):
        raise HostingError("Arguments may only contain letters, numbers and . _ = : / - (no shell symbols).")
    return command
