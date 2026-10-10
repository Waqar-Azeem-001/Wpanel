"""
WHPanel's side of backup storage (N8): remember where a node's encrypted backups go, tell the node, and back up the node's own configuration.

The storage keys and the backup password are kept encrypted and are write-only: staff can replace them but never read them back. Only the node that uses them receives them, over the signed
command channel, and the command's copy is overwritten with ``***`` when it finishes.
"""
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from . import catalog, services
from .models import AgentNode, BackupRepository, NodeStatus


def save_repository(user, node, *, endpoint, bucket, prefix, access_key="", secret_key="", password="", object_lock=False, request=None):
    """Create or change a node's repository settings. Keys left blank are kept as they were (so staff can change the bucket without re-typing secrets)."""
    repo = BackupRepository.objects.filter(node=node).first() or BackupRepository(node=node)
    try:  # the same checks the node applies, so staff hear about a typo now
        catalog.validate("BACKUP_CONFIGURE", {"endpoint": endpoint, "bucket": bucket, "prefix": prefix, "access_key": access_key or "x" * 12,
                                              "secret_key": secret_key or "x" * 12, "repo_password": password or "x" * 12})
    except catalog.InvalidCommand as exc:
        raise ServiceError(str(exc)) from None
    if repo.pk is None and not (access_key and secret_key and password):
        raise ServiceError("Enter the access key, secret key and backup password.")
    if repo.pk and password and repo.password_encrypted:
        raise ServiceError("The backup password cannot be changed once set: existing backups could no longer be read. Use a new prefix for a new repository.")
    old = repo.secrets() if repo.pk else ("", "", "")
    repo.endpoint, repo.bucket, repo.prefix, repo.object_lock_confirmed = endpoint, bucket, prefix, bool(object_lock)
    repo.set_secrets(access_key or old[0], secret_key or old[1], password or old[2])
    repo.applied_ok = False  # the node has to be told again
    repo.save()
    audit.record("agent.backup_repository_saved", actor=user, target=node, request=request, metadata={"bucket": bucket, "prefix": prefix, "object_lock": bool(object_lock)})
    return repo


def apply(user, repo, request=None):
    """Send the settings to the node (queued). The outcome arrives through ``job_finished``."""
    if repo.node.status != NodeStatus.ENROLLED:
        raise ServiceError("This node is not enrolled.")
    access, secret, password = repo.secrets()
    job = services.dispatch(user, repo.node.server, "BACKUP_CONFIGURE", {"endpoint": repo.endpoint, "bucket": repo.bucket, "prefix": repo.prefix, "access_key": access,
                                                                         "secret_key": secret, "repo_password": password}, subject=f"backupcfg:{repo.node_id}", request=request)
    audit.record("agent.backup_repository_applied", actor=user, target=repo.node, request=request, metadata={"job": job.pk})
    return job


def backup_node_config(user, node, request=None):
    repo = getattr(node, "backup_repository", None)
    if repo is None or not repo.applied_ok:
        raise ServiceError("Set up and apply the backup storage for this node first.")
    return services.dispatch(user, node.server, "BACKUP_NODE_CONFIG", {}, subject=f"backupcfg:{node.pk}", request=request)


def job_finished(job):
    node = AgentNode.objects.filter(pk=int(job.subject.split(":", 1)[1])).first()
    repo = getattr(node, "backup_repository", None) if node else None
    if repo is None:
        return
    ok = job.result_ok is True
    if job.command == "BACKUP_CONFIGURE":
        repo.applied_ok, repo.applied_at, repo.apply_error = ok, timezone.now(), "" if ok else (job.result_message or job.get_status_display())[:300]
        repo.save(update_fields=["applied_ok", "applied_at", "apply_error", "updated_at"])
        audit.record("agent.backup_repository_" + ("ready" if ok else "failed"), target=node, metadata={"error": repo.apply_error})
    elif job.command == "BACKUP_NODE_CONFIG" and ok:
        repo.node_config_at, repo.node_config_snapshot = timezone.now(), job.result_data.get("snapshot_id", "")
        repo.save(update_fields=["node_config_at", "node_config_snapshot", "updated_at"])
    elif job.command == "BACKUP_NODE_CONFIG":
        audit.record("agent.node_config_backup_failed", target=node, metadata={"error": job.result_message})
