"""
Manual adapter: simulates hosting provisioning entirely within our own
database. This is NOT a real WHM integration - it makes no network calls.
See ``apps.domains.adapters.manual`` for why this pattern exists: it lets the
full lifecycle (request, provision, suspend, terminate, change package) be
built, demonstrated and tested end to end before a real server is connected.
A production deployment must configure a real server (kind=WHM_API) before
going live.
"""
import uuid

from .base import HostingAdapter


class ManualAdapter(HostingAdapter):
    def create_account(self, *, username, domain, package, contact_email, password):
        return {"provider_ref": f"manual-{uuid.uuid4().hex[:12]}"}

    def suspend_account(self, username, reason=""):
        pass

    def unsuspend_account(self, username):
        pass

    def terminate_account(self, username, *, keep_dns=False):
        pass

    def change_package(self, username, package):
        pass

    def get_status(self, username):
        from apps.hosting.models import HostingAccount, HostingStatus

        account = HostingAccount.objects.filter(username=username).first()
        if account is None:
            return {"suspended": False, "domain": ""}
        return {"suspended": account.status == HostingStatus.SUSPENDED, "domain": account.domain}

    def get_usage(self, username):
        # No real monitoring behind this adapter - reported as unknown, not zero.
        return {"disk_used_mb": None, "disk_limit_mb": None, "bandwidth_used_mb": None, "bandwidth_limit_mb": None}
