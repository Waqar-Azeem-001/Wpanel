"""
Native provider (N3): hosting on a node WHPanel runs itself, through the WH Agent, with no cPanel.

Nothing here talks to the node. Each operation queues an allow-listed, signed command (apps.agent) that the node's agent fetches. Creating an account
is therefore not finished when ``create_account`` returns: it returns ``pending`` and the account becomes active when the agent reports back
(``apps.hosting.native.job_finished``). Suspend, unsuspend, terminate, backup and package changes are queued the same way and recorded straight away; if the
node later reports a failure, the account's ``last_error`` and the audit trail say so.
"""

from .base import HostingAdapter, HostingError
from .native_cron import NativeCron
from .native_db import NativeDatabases
from . import native_dns
from .native_backups import NativeBackups
from .native_dns import NativeDns
from .native_files import NativeFiles, NativeFtp
from .native_insights import NativeInsights
from .native_mail import NativeEmail
from .native_ssl import NativeSsl

DEFAULT_PHP = "8.3"
LIMIT_KEYS = ("disk_mb", "memory_mb", "processes", "cpu_percent")
COUNT_KEYS = {"databases": "databases", "ftp_accounts": "sftp_accounts"}  # the plan's name -> the node's name
UNLIMITED = 1000


class NativeAdapter(NativeCron, NativeInsights, NativeBackups, NativeEmail, NativeFiles, NativeFtp, NativeDatabases, NativeDns, NativeSsl, HostingAdapter):
    server = None  # set by the registry
    read_cache_seconds = 20  # every read is a trip to the node; the gateway remembers answers briefly and forgets them after any change

    @property
    def enabled_capabilities(self):
        """Everything this provider has, except DNS when no nameservers are configured (the screen is then not offered, instead of failing)."""
        from .capabilities import CONTRACTS, Capability

        have = {c for c, contract in CONTRACTS.items() if isinstance(self, contract)}
        if native_dns.get_dns() is None:
            have.discard(Capability.DNS)
        from apps.agent.placement import has_mail_nodes

        if not has_mail_nodes():
            have.discard(Capability.EMAIL)  # nobody could hold the mailboxes
        return have

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
        found = {k: limits[k] for k in LIMIT_KEYS if isinstance(limits.get(k), int) and not isinstance(limits.get(k), bool) and limits[k] > 0}
        for plan_key, node_key, ceiling in (("inodes", "inodes", 50_000_000), ("io_weight", "io_weight", None)):
            value = limits.get(plan_key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                found[node_key] = value
            elif plan_key in limits and value is None and ceiling:
                found[node_key] = ceiling  # a plan that says "no limit on files" still gets the largest the node accepts
        for plan_key, node_key in COUNT_KEYS.items():
            if plan_key in limits:  # the plan says how many: a number (0 means none), or blank for unlimited
                value = limits[plan_key]
                found[node_key] = UNLIMITED if value is None else value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 5
        return found

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
        extra = {k: limits[k] for k in COUNT_KEYS.values() if k in limits}
        self._queue("SET_LIMITS", {"account": username, **{k: limits.get(k, defaults[k]) for k in LIMIT_KEYS}, **extra}, username)
        account = self._account(username)
        if account and account.mail_server_id:
            self.sync_mail_limits(username, package)  # the mail node's limits follow the plan too

    def get_status(self, username):
        from apps.hosting.models import HostingStatus

        account = self._account(username)
        return {"suspended": bool(account and account.status == HostingStatus.SUSPENDED), "domain": account.domain if account else ""}

    def get_usage(self, username):
        from apps.agent import services as agent
        from apps.core.exceptions import ServiceError
        from apps.products.models import Product

        account = self._account(username)
        try:
            data = agent.call(self.server, "USAGE_REPORT", {"account": username, "days": 1}, wait=60)
        except ServiceError as exc:
            raise HostingError(exc.message) from exc
        product = Product.objects.filter(whm_package_name=account.package_name).first() if account else None
        plan = product.resource_limits if product and isinstance(product.resource_limits, dict) else {}
        bandwidth_limit = plan.get("bandwidth_mb")
        return {"disk_used_mb": round(data["now"]["disk_mb"] + data["now"]["db_mb"]), "disk_limit_mb": data["limits"]["disk_mb"],  # database data counts against the disk allowance too
                "bandwidth_used_mb": round(data["now"]["bandwidth_month_mb"]),
                "bandwidth_limit_mb": bandwidth_limit if isinstance(bandwidth_limit, int) and not isinstance(bandwidth_limit, bool) and bandwidth_limit > 0 else None}


