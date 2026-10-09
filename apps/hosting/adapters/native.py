"""
Native provider (N3): hosting on a node WHPanel runs itself, through the WH Agent, with no cPanel.

Nothing here talks to the node. Each operation queues an allow-listed, signed command (apps.agent) that the node's agent fetches. Creating an account
is therefore not finished when ``create_account`` returns: it returns ``pending`` and the account becomes active when the agent reports back
(``apps.hosting.native.job_finished``). Suspend, unsuspend, terminate, backup and package changes are queued the same way and recorded straight away; if the
node later reports a failure, the account's ``last_error`` and the audit trail say so.
"""

from .base import HostingAdapter, HostingError

DEFAULT_PHP = "8.3"
LIMIT_KEYS = ("disk_mb", "memory_mb", "processes", "cpu_percent")


class NativeAdapter(HostingAdapter):
    server = None  # set by the registry

    # --- helpers ---------------------------------------------------------------------------------------------------------------------
    def _account(self, username):
        from apps.hosting.models import LIVE_STATUSES, HostingAccount

        account = HostingAccount.objects.filter(server=self.server, username=username).order_by("-id").first()
        live = HostingAccount.objects.filter(server=self.server, username=username, status__in=LIVE_STATUSES).order_by("-id").first()
        return live or account

    def _limits(self, package):
        from apps.products.models import Product

        product = Product.objects.filter(whm_package_name=package).first()
        limits = product.resource_limits if product and isinstance(product.resource_limits, dict) else {}
        return {k: limits[k] for k in LIMIT_KEYS if isinstance(limits.get(k), int) and not isinstance(limits.get(k), bool) and limits[k] > 0}

    def _queue(self, command, params, username, key=None):
        from apps.agent import services as agent
        from apps.core.exceptions import ServiceError

        account = self._account(username)
        try:
            return agent.dispatch(None, self.server, command, params, idempotency_key=key, subject=f"hosting:{account.pk}" if account else "")
        except ServiceError as exc:
            raise HostingError(exc.message) from exc

    # --- the adapter contract ----------------------------------------------------------------------------------------------------------
    def check_connection(self):
        from apps.agent import enrollment

        from apps.agent.models import AgentNode

        node = AgentNode.objects.filter(server=self.server).first()
        if node is None:
            raise HostingError("No agent has been set up for this server. Add it under Native nodes.")
        return {"version": node.agent_version, "packages": [], "state": enrollment.health_state(node)}

    def create_account(self, *, username, domain, package, contact_email, password):
        from apps.agent.models import AgentJob

        subject = f"hosting:{self._account(username).pk}"
        earlier = AgentJob.objects.filter(node__server=self.server, subject=subject, command="CREATE_WEBSITE")
        running = earlier.filter(status__in=("queued", "leased")).first()
        if running:  # pressing the button twice must not queue a second website
            return {"provider_ref": f"native-job-{running.pk}", "pending": True}
        attempt = earlier.count() + 1  # a retry after a failure is a new job
        job = self._queue("CREATE_WEBSITE", {"account": username, "domain": domain.lower(), "php_version": DEFAULT_PHP, **self._limits(package)}, username,
                          key=f"create-{subject}-{attempt}")
        return {"provider_ref": f"native-job-{job.pk}", "pending": True}  # the password is not used: there is no separate control panel to log in to

    def suspend_account(self, username, reason=""):
        self._queue("SUSPEND_WEBSITE", {"account": username}, username)

    def unsuspend_account(self, username):
        self._queue("UNSUSPEND_WEBSITE", {"account": username}, username)

    def terminate_account(self, username, *, keep_dns=False):
        account = self._account(username)
        self._queue("DELETE_WEBSITE", {"account": username, "domain": account.domain.lower(), "keep_archive": "yes"}, username)

    def backup_account(self, username):
        job = self._queue("CREATE_BACKUP", {"account": username, "scope": "full"}, username)
        return {"backup_ref": f"native-job-{job.pk}"}

    def change_package(self, username, package):
        limits = self._limits(package)
        if not limits:
            raise HostingError("That plan sets no resource limits the node can apply.")
        defaults = {"disk_mb": 5120, "memory_mb": 1024, "processes": 100, "cpu_percent": 100}
        self._queue("SET_LIMITS", {"account": username, **{k: limits.get(k, defaults[k]) for k in LIMIT_KEYS}}, username)

    def get_status(self, username):
        from apps.hosting.models import HostingStatus

        account = self._account(username)
        return {"suspended": bool(account and account.status == HostingStatus.SUSPENDED), "domain": account.domain if account else ""}

    def get_usage(self, username):
        return {"disk_used_mb": None, "disk_limit_mb": None, "bandwidth_used_mb": None, "bandwidth_limit_mb": None}  # N3 has no usage command yet: not reported, not zero


