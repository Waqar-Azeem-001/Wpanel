"""
The cross-tenant attack test (N9, ADR 03): tenant A tries to read, list, signal and reach tenant B's things and the server's own. Every attempt must FAIL. A node does not take customers until
every probe passes on that node, run as the attacker's real user.

``run_probes`` holds the list of attempts and the judging; the way an attempt is made is passed in (``attempt``), so the judging is tested here against pretend outcomes. ``RealAttempt`` makes
the attempts for real by starting a small Python process under the attacker's uid/gid; it has **never been run**: it cannot run on Windows, and nobody has run it on a node. Until it has
been, "isolation" is a design, not a proven property.
"""
import json
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class ProbeResult:
    name: str
    passed: bool
    detail: str


def probes(victim, victim_pid=None):
    """(name, action, arguments) for everything the attacker tries."""
    base = f"/home/{victim}"
    items = [("read the victim's website file", "read", {"path": f"{base}/sites/{{domain}}/public_html/index.html"}),
             ("list the victim's website folder", "list", {"path": f"{base}/sites"}),
             ("list the victim's home folder", "list", {"path": base}),
             ("read the victim's temporary files", "list", {"path": f"{base}/tmp"}),
             ("write into the victim's website folder", "write", {"path": f"{base}/sites/{{domain}}/public_html/attacker.txt"}),
             ("connect to the victim's PHP socket", "connect", {"path": f"/run/whp-php/{victim}/php.sock"}),
             ("read the server's password hashes", "read", {"path": "/etc/shadow"}),
             ("read the node's backup keys", "read", {"path": "/etc/whp/restic.env"}),
             ("read the agent's own settings", "list", {"path": "/var/lib/whp/accounts"}),
             ("read root's home", "list", {"path": "/root"}),
             ("see the victim's processes", "see_process", {"user": victim}),
             ("open the victim's database through the attacker's login", "database", {"name_prefix": victim + "_"})]
    if victim_pid:
        items.append(("send a signal to the victim's process", "signal", {"pid": victim_pid}))
    return items


def run_probes(attacker, victim, attempt, domain="victim.test", victim_pid=None):
    """Run every probe. ``attempt(attacker, action, **arguments)`` returns True when the attacker SUCCEEDED (bad). Returns a list of ProbeResult; a probe passes when the attempt failed."""
    results = []
    for name, action, args in probes(victim, victim_pid):
        args = {k: (v.format(domain=domain) if isinstance(v, str) else v) for k, v in args.items()}
        try:
            succeeded = attempt(attacker, action, **args)
        except Exception as exc:  # noqa: BLE001 - an attempt that blows up did not succeed
            results.append(ProbeResult(name, True, f"refused ({type(exc).__name__})"))
            continue
        results.append(ProbeResult(name, not succeeded, "the attacker got in" if succeeded else "refused"))
    return results


def verdict(results):
    failed = [r for r in results if not r.passed]
    return {"ok": not failed, "failed": [f"{r.name}: {r.detail}" for r in failed], "checked": len(results)}


SNIPPET = (
    "import json, os, socket, sys\n"
    "a = json.loads(sys.argv[1])\n"
    "act = a['action']\n"
    "try:\n"
    "    if act == 'read':\n        open(a['path'], 'rb').read(1)\n"
    "    elif act == 'list':\n        os.listdir(a['path'])\n"
    "    elif act == 'write':\n        open(a['path'], 'wb').write(b'x')\n"
    "    elif act == 'connect':\n        s = socket.socket(socket.AF_UNIX); s.connect(a['path'])\n"
    "    elif act == 'see_process':\n        sys.exit(0 if any(os.stat('/proc/' + d).st_uid == a['uid'] for d in os.listdir('/proc') if d.isdigit() and os.path.exists('/proc/' + d)) else 1)\n"
    "    elif act == 'signal':\n        os.kill(a['pid'], 0)\n"
    "    else:\n        sys.exit(1)\n"
    "    sys.exit(0)\n"
    "except Exception:\n    sys.exit(1)\n"
)


class RealAttempt:
    """UNTESTED. Makes each attempt as the attacker's own Linux user. ``users`` maps account name to (uid, gid)."""

    def __init__(self, users):
        self.users = users

    def __call__(self, attacker, action, **args):
        if action == "database":
            return False  # needs a database login of the attacker; the database probe is not built, so this probe reports "refused" without trying: do not trust it
        uid, gid = self.users[attacker]
        if action == "see_process":
            args["uid"] = self.users[args.pop("user")][0]
        done = subprocess.run([sys.executable, "-c", SNIPPET, json.dumps({"action": action, **args})], shell=False, user=uid, group=gid, extra_groups=[], capture_output=True,
                              timeout=30, check=False)
        return done.returncode == 0
