"""PHP on a cPanel server (EasyApache), through UAPI's ``LangPHP`` module: the version per domain and the few settings a customer may change."""
import re

from .base import HostingError
from .capabilities import PhpCapability, PhpOptions, PhpSettings
from .cpanel_api import CpanelApi

END_OF_LIFE = {"5.6", "7.0", "7.1", "7.2", "7.3", "7.4", "8.0"}
MEMORY = ["128M", "256M", "512M", "1024M"]
UPLOAD = ["16M", "32M", "64M", "128M", "256M"]
EXECUTION = [30, 60, 120, 300]
DIRECTIVES = {"memory_limit", "upload_max_filesize", "max_execution_time", "display_errors"}
PACKAGE = re.compile(r"^ea-php(\d)(\d+)$")


def version_of(package):
    match = PACKAGE.match(str(package))
    return f"{match.group(1)}.{match.group(2)}" if match else ""


def package_of(version):
    return "ea-php" + str(version).replace(".", "")


def megabytes(value):
    return int(str(value).rstrip("M"))


class CpanelPhp(CpanelApi, PhpCapability):
    def _installed(self, user):
        data = self._uapi(user, "LangPHP", "php_get_installed_versions") or {}
        packages = data.get("versions") if isinstance(data, dict) else data
        versions = sorted({version_of(p) for p in packages or []} - {""}, key=lambda v: [int(x) for x in v.split(".")], reverse=True)
        return versions

    def php_options(self, username):
        versions = self._installed(username)
        supported = [v for v in versions if v not in END_OF_LIFE]
        return PhpOptions(versions=versions, end_of_life=[v for v in versions if v in END_OF_LIFE], recommended=supported[0] if supported else "",
                          memory_limits=list(MEMORY), upload_limits=list(UPLOAD), execution_times=list(EXECUTION))

    def _version(self, user, domain):
        for item in self._uapi(user, "LangPHP", "php_get_vhost_versions") or []:
            if isinstance(item, dict) and str(item.get("vhost", "")).lower() == domain:
                return version_of(item.get("version"))
        return ""

    def _directives(self, user, domain):
        data = self._uapi(user, "LangPHP", "php_ini_get_user_basic_directives", {"type": "vhost", "vhost": domain}) or {}
        items = data.get("directives") if isinstance(data, dict) else data
        if isinstance(items, dict):
            return {str(k): str(v) for k, v in items.items()}
        return {str(i.get("key")): str(i.get("value")) for i in items or [] if isinstance(i, dict) and i.get("key")}

    def get_php_settings(self, username, domain):
        domain = self._owned_domain(username, domain)
        found = self._directives(username, domain)
        try:
            execution = int(found.get("max_execution_time", ""))
        except ValueError:
            execution = None
        return PhpSettings(version=self._version(username, domain), memory_limit=found.get("memory_limit", ""),
                           upload_max_filesize=found.get("upload_max_filesize", ""), max_execution_time=execution,
                           display_errors=found.get("display_errors", "").lower() in ("on", "1", "true"), available_versions=self._installed(username))

    def set_php_settings(self, username, domain, settings):
        domain = self._owned_domain(username, domain)
        unknown = [k for k in settings or {} if k not in DIRECTIVES | {"version"}]
        if unknown or not settings:
            raise HostingError("That setting cannot be changed here.")
        options = self.php_options(username)
        directives = {}
        for key, value in settings.items():
            if key == "version":
                if value not in options.versions:
                    raise HostingError("Choose one of the listed PHP versions.")
            elif key == "memory_limit":
                if value not in MEMORY:
                    raise HostingError("Choose one of the listed memory limits.")
                directives[key] = value
            elif key == "upload_max_filesize":
                if value not in UPLOAD:
                    raise HostingError("Choose one of the listed upload limits.")
                directives[key] = value
            elif key == "max_execution_time":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise HostingError("Choose one of the listed run times.")
                if value not in EXECUTION:
                    raise HostingError("Choose one of the listed run times.")
                directives[key] = str(value)
            elif key == "display_errors":
                directives[key] = "On" if value in (True, "1", "on", "true", 1) else "Off"
        current = self._directives(username, domain)
        memory, upload = directives.get("memory_limit", current.get("memory_limit", "256M")), directives.get("upload_max_filesize", current.get("upload_max_filesize", "64M"))
        try:
            if megabytes(upload) > megabytes(memory):
                raise HostingError("The upload limit cannot be larger than the memory limit. Raise the memory limit, or lower the upload limit.")
        except ValueError:
            pass  # a value we cannot read from the server is not a reason to refuse the customer's own choice
        if "version" in settings:
            self._uapi(username, "LangPHP", "php_set_vhost_versions", {"version": package_of(settings["version"]), "vhost": domain})
        if directives:
            params = {"type": "vhost", "vhost": domain}
            params.update({f"directive-{i}": f"{k}:{v}" for i, (k, v) in enumerate(sorted(directives.items()), 1)})
            self._uapi(username, "LangPHP", "php_ini_set_user_basic_directives", params)

    # cPanel's own PHP extensions are chosen by the server owner, not the account, so there is nothing to list or switch here.
    def list_php_extensions(self, username, domain):
        self._unsupported("PHP extensions")

    def set_php_extension(self, username, domain, name, enabled):
        self._unsupported("PHP extensions")
