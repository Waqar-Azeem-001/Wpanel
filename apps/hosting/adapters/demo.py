"""
Demo adapter: a provider that answers every hosting capability it supports with fixed SAMPLE data, so the customer
screens (Websites dashboard now, files/DNS/SSL/PHP... as they are built) can be seen working, designed and tested before
any real server is connected. Like the manual adapter it makes no network calls; unlike it, it reports sample usage,
SSL and PHP details. It is for development and demonstration only - never a production server kind - and everything it
reports says so ("Sample data").

Sample values are derived from the account's username, so they are stable between page loads and differ between accounts.
"""
from .capabilities import PhpCapability, PhpSettings, SslCapability, SslStatus
from .manual import ManualAdapter

PHP_VERSIONS = ["7.4", "8.0", "8.1", "8.2", "8.3"]


def _seed(username):
    return sum(ord(c) for c in username)


class DemoAdapter(SslCapability, PhpCapability, ManualAdapter):
    def get_usage(self, username):
        seed = _seed(username)
        disk_limit = 20480
        bandwidth_limit = 102400
        return {"disk_used_mb": (seed * 37) % disk_limit, "disk_limit_mb": disk_limit,
                "bandwidth_used_mb": (seed * 91) % bandwidth_limit, "bandwidth_limit_mb": bandwidth_limit}

    # --- SSL ---------------------------------------------------------------------------------------------------

    def get_ssl_status(self, username, domain):
        active = _seed(username) % 5 != 0  # about one account in five shows "not active"
        return SslStatus(domain=domain, active=active, issuer="Sample data" if active else "",
                         expires_at="2027-01-15T00:00:00Z" if active else None, auto_renew=active,
                         https_redirect=active)

    def request_ssl(self, username, domain):
        pass

    def set_https_redirect(self, username, domain, enabled):
        pass

    # --- PHP ---------------------------------------------------------------------------------------------------

    def get_php_settings(self, username, domain):
        version = PHP_VERSIONS[_seed(username) % len(PHP_VERSIONS)]
        return PhpSettings(version=version, memory_limit="256M", upload_max_filesize="64M", max_execution_time=60,
                           display_errors=False, available_versions=PHP_VERSIONS)

    def set_php_settings(self, username, domain, settings):
        pass
