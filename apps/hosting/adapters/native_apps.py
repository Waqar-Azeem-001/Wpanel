"""
Installing WordPress on a native node: the agent does it all (database, user, checked downloads, configuration, administrator) in one command. It takes minutes, so the command is
queued and this returns "installing"; WHPanel is told when the node finishes (``apps.hosting.site_apps.job_finished``) and the screen shows the progress until then.
"""
from .base import HostingError
from .capabilities import AppsCapability


class NativeApps(AppsCapability):
    def install_wordpress(self, username, domain, title, admin_user, admin_password, admin_email):
        from apps.agent import services as agent
        from apps.core.exceptions import ServiceError

        account = self._account(username)
        if account is None:
            raise HostingError("No such hosting account on this server.")
        params = {"account": username, "domain": domain.lower(), "site_title": title, "admin_user": admin_user, "admin_email": admin_email, "admin_password": admin_password}
        try:
            job = agent.dispatch(None, self.server, "INSTALL_WORDPRESS", params, subject=f"app:{account.pk}", idempotency_key=f"wp-{account.pk}-{AppsCounter.next(account.pk)}")
        except ServiceError as exc:
            raise HostingError(exc.message) from exc
        return {"state": "installing", "job": job.pk}


class AppsCounter:
    """A new idempotency key for each attempt (a retry after a failure is a new job, a double click is not)."""

    @staticmethod
    def next(account_pk):
        from apps.agent.models import AgentJob

        return AgentJob.objects.filter(command="INSTALL_WORDPRESS", subject=f"app:{account_pk}").count() + 1
