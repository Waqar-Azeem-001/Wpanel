"""
The node's hands (N3). Everything the website handlers do to the machine goes through this small interface, so the same handler code can be tested
against ``FakeSystem`` (files in memory, commands recorded) and run for real with ``RealSystem``.

Rules both obey: a program is run from an **argument list**, never through a shell; a path must be absolute and inside the allowed roots.

``RealSystem`` has NOT been run on a real AlmaLinux node. It is a straightforward subprocess/file wrapper but is untested.
"""
import os
import posixpath
import subprocess

READ_ONLY_ROOTS = ("/proc/", "/sys/fs/cgroup/")  # the agent may read these (to check isolation and measure use), never write them
ALLOWED_ROOTS = ("/home/", "/etc/", "/var/lib/whp/", "/var/log/whp/", "/var/backups/whp/", "/run/whp/", "/srv/whp/", "/var/mail/")


class SystemError_(Exception):
    """A step failed on the machine."""


def check_path(path, read_only=False):
    clean = posixpath.normpath(path)
    if not clean.startswith("/") or clean != path.rstrip("/") or ".." in path.split("/") or "\x00" in path:
        raise SystemError_(f"Refused path {path!r}.")
    if not any((clean + "/").startswith(root) for root in ALLOWED_ROOTS + (READ_ONLY_ROOTS if read_only else ())):
        raise SystemError_(f"Path {path!r} is outside the places the agent may touch.")
    return clean


class FakeSystem:
    """A pretend machine: a dictionary of files, a list of every command run, and users with ids. ``fail_on`` makes the Nth command fail (tests)."""

    def __init__(self):
        self.files, self.dirs, self.users, self.commands = {}, set(), {}, []
        self.next_uid = 2000
        self.fail_on = None
        self.service_state = {}  # unit name -> "active", "inactive", "activating" ... (what ``systemctl is-active`` would say)
        self.probes = {}  # argv tuple -> output, for other read-only questions

    def exists(self, path):
        return check_path(path) in self.files or check_path(path) in self.dirs

    def read(self, path):
        return self.files.get(check_path(path, read_only=True))

    def list(self, prefix):
        """Paths of the files under a folder."""
        return sorted(p for p in self.files if p.startswith(check_path(prefix.rstrip('/')) + '/'))

    def write(self, path, content, mode=0o644, owner="root", group=None):
        self.files[check_path(path)] = content
        self.commands.append(["write", path, oct(mode), owner] + ([group] if group else []))

    def mkdir(self, path, mode=0o755, owner="root", group=None):
        self.dirs.add(check_path(path))
        self.commands.append(["mkdir", path, oct(mode), owner] + ([group] if group else []))

    def remove(self, path):
        clean = check_path(path)
        for key in [k for k in self.files if k == clean or k.startswith(clean + "/")]:
            del self.files[key]
        self.dirs = {d for d in self.dirs if d != clean and not d.startswith(clean + "/")}
        self.commands.append(["remove", path])

    def uid(self, user):
        return self.users.get(user)

    def probe(self, argv):
        """Ask a question that may have a negative answer. Returns (exit status, output); never raises for a non-zero exit status."""
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            raise SystemError_("A command must be a list of strings.")
        if argv[:2] == ["systemctl", "is-active"]:
            state = self.service_state.get(argv[2], "inactive")
            return (0 if state == "active" else 3), state
        out = self.probes.get(tuple(argv))
        return (0, out) if out is not None else (127, "")

    def run(self, argv, input=None):
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            raise SystemError_("A command must be a list of strings.")
        if self.fail_on is not None and len(self.commands) == self.fail_on:
            self.commands.append(["FAILED", *argv])
            raise SystemError_(f"{argv[0]} failed")
        self.commands.append(argv)
        if argv[:1] == ["useradd"]:
            self.users[argv[-1]] = self.next_uid
            self.next_uid += 1
        elif argv[:1] == ["userdel"]:
            self.users.pop(argv[-1], None)
            if "--remove" in argv:  # the real userdel --remove deletes the home folder too
                self.files = {k: v for k, v in self.files.items() if not k.startswith(f"/home/{argv[-1]}/")}
                self.dirs = {d for d in self.dirs if d != f"/home/{argv[-1]}" and not d.startswith(f"/home/{argv[-1]}/")}
        return ""


class RealSystem:
    """The real thing: UNTESTED. Run only as root on a node, never through a shell."""

    def exists(self, path):
        return os.path.exists(check_path(path))

    def list(self, prefix):
        base = check_path(prefix.rstrip('/'))
        found = []
        for folder, _dirs, names in os.walk(base):
            found += [os.path.join(folder, name).replace("\\", "/") for name in names]
        return sorted(found)

    def read(self, path):
        try:
            with open(check_path(path, read_only=True), encoding="utf-8") as handle:
                return handle.read()
        except FileNotFoundError:
            return None

    def write(self, path, content, mode=0o644, owner="root", group=None):
        clean = check_path(path)
        os.makedirs(posixpath.dirname(clean), exist_ok=True)
        temp = clean + ".whp-tmp"
        with open(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w", encoding="utf-8") as handle:
            handle.write(content)
        if owner != "root" or group:
            self.run(["chown", f"{owner}:{group or owner}", temp])
        os.replace(temp, clean)  # a half-written file is never visible

    def mkdir(self, path, mode=0o755, owner="root", group=None):
        clean = check_path(path)
        os.makedirs(clean, mode=mode, exist_ok=True)
        os.chmod(clean, mode)
        if owner != "root" or group:
            self.run(["chown", f"{owner}:{group or owner}", clean])
            os.chmod(clean, mode)  # chown clears the setgid bit on some systems: set the mode again afterwards

    def remove(self, path):
        clean = check_path(path)
        if clean in ("/home", "/etc", "/var/lib/whp") or clean.count("/") < 3:
            raise SystemError_("Refusing to remove a top-level folder.")
        self.run(["rm", "-rf", "--one-file-system", "--", clean])

    def uid(self, user):
        import pwd

        try:
            return pwd.getpwnam(user).pw_uid
        except KeyError:
            return None

    def probe(self, argv):
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            raise SystemError_("A command must be a list of strings.")
        try:
            done = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return 127, ""
        return done.returncode, done.stdout.strip()

    def run(self, argv, input=None):
        """``input`` goes to the program's standard input (used for passwords, which must never be on a command line)."""
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            raise SystemError_("A command must be a list of strings.")
        done = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=300, check=False, input=input)
        if done.returncode != 0:
            raise SystemError_(f"{argv[0]} failed: {done.stderr.strip()[:200]}")
        return done.stdout
