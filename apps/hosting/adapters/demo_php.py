"""
The demo provider's PHP settings: a JSON file per account that behaves like a shared-hosting PHP selector. Each domain has its own
version, memory limit, upload limit, run time, error display and extensions - but only from the plan's lists of choices, never a
raw configuration file. An extension the website depends on cannot be switched off, and the upload limit cannot be larger than the
memory limit. No PHP runs here; a real provider (the cPanel adapter, a native node) implements the same ``PhpCapability`` contract.
"""
import json
from pathlib import Path

from .base import HostingError
from .capabilities import PhpExtension, PhpOptions, PhpSettings

VERSIONS = ["8.3", "8.2", "8.1", "8.0", "7.4"]
END_OF_LIFE = ["8.0", "7.4"]
RECOMMENDED = "8.3"
MEMORY = ["128M", "256M", "512M", "1024M"]
UPLOAD = ["16M", "32M", "64M", "128M", "256M"]
EXECUTION = [30, 60, 120, 300]
EXTENSIONS = ["bcmath", "curl", "exif", "fileinfo", "gd", "imagick", "intl", "mbstring", "mysqli", "opcache", "pdo_mysql", "soap",
              "xml", "zip"]
REQUIRED = {"mysqli", "mbstring"}
DEFAULT_ON = set(EXTENSIONS) - {"bcmath", "imagick", "soap"}
SETTING_KEYS = {"version", "memory_limit", "upload_max_filesize", "max_execution_time", "display_errors"}


def megabytes(value):
    return int(str(value).rstrip("M"))


class DemoPhp:
    def __init__(self, directory, domains):
        self.file = Path(directory) / "php.json"
        self.domains = domains  # callback: the names of every domain on the account

    def _load(self):
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(data), encoding="utf-8")

    def _known(self, domain):
        domain = (domain or "").strip().lower().rstrip(".")
        if domain not in self.domains():
            raise HostingError("That domain is not on this account.")
        return domain

    @staticmethod
    def _fresh(domain):
        return {"version": VERSIONS[sum(map(ord, domain)) % 3],
                "memory_limit": "256M", "upload_max_filesize": "64M", "max_execution_time": 60, "display_errors": False,
                "extensions": {name: name in DEFAULT_ON for name in EXTENSIONS}}

    def _entry(self, data, domain):
        entry = data.get(domain) or self._fresh(domain)
        entry.setdefault("extensions", {name: name in DEFAULT_ON for name in EXTENSIONS})
        return entry

    def options(self):
        return PhpOptions(versions=list(VERSIONS), end_of_life=list(END_OF_LIFE), recommended=RECOMMENDED,
                          memory_limits=list(MEMORY), upload_limits=list(UPLOAD), execution_times=list(EXECUTION))

    def settings(self, domain):
        domain = self._known(domain)
        entry = self._entry(self._load(), domain)
        return PhpSettings(version=entry["version"], memory_limit=entry["memory_limit"],
                           upload_max_filesize=entry["upload_max_filesize"], max_execution_time=entry["max_execution_time"],
                           display_errors=entry["display_errors"], available_versions=list(VERSIONS))

    def set_settings(self, domain, changes):
        domain = self._known(domain)
        unknown = [k for k in (changes or {}) if k not in SETTING_KEYS]
        if unknown or not changes:
            raise HostingError("That setting cannot be changed here.")
        data = self._load()
        entry = self._entry(data, domain)
        new = dict(entry)
        for key, value in changes.items():
            if key == "version":
                if value not in VERSIONS:
                    raise HostingError("Choose one of the listed PHP versions.")
            elif key == "memory_limit":
                if value not in MEMORY:
                    raise HostingError("Choose one of the listed memory limits.")
            elif key == "upload_max_filesize":
                if value not in UPLOAD:
                    raise HostingError("Choose one of the listed upload limits.")
            elif key == "max_execution_time":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise HostingError("Choose one of the listed run times.")
                if value not in EXECUTION:
                    raise HostingError("Choose one of the listed run times.")
            elif key == "display_errors":
                value = value in (True, "1", "on", "true", 1)
            new[key] = value
        if megabytes(new["upload_max_filesize"]) > megabytes(new["memory_limit"]):
            raise HostingError("The upload limit cannot be larger than the memory limit. Raise the memory limit, or lower the upload limit.")
        data[domain] = new
        self._save(data)

    def extensions(self, domain):
        domain = self._known(domain)
        enabled = self._entry(self._load(), domain)["extensions"]
        return [PhpExtension(name=n, enabled=bool(enabled.get(n)) or n in REQUIRED, required=n in REQUIRED) for n in EXTENSIONS]

    def set_extension(self, domain, name, enabled):
        domain = self._known(domain)
        if name not in EXTENSIONS:
            raise HostingError("That PHP extension is not available.")
        if name in REQUIRED and not enabled:
            raise HostingError(f"'{name}' is needed by your website and the control panel, so it stays on.")
        data = self._load()
        entry = self._entry(data, domain)
        entry["extensions"][name] = bool(enabled)
        data[domain] = entry
        self._save(data)
