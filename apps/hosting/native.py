"""What happens in WHPanel when the node reports the outcome of a hosting job (N3)."""
from apps.audit import services as audit
from apps.notifications import services as notifications

from .models import HostingAccount, HostingStatus


def job_finished(job):
    try:
        account = HostingAccount.objects.select_related("client").get(pk=int(job.subject.split(":", 1)[1]))
    except (ValueError, HostingAccount.DoesNotExist):
        return
    ok = job.result_ok is True
    if job.command == "CREATE_WEBSITE":
        if ok and account.status in (HostingStatus.PENDING, HostingStatus.FAILED):
            account.status, account.last_error = HostingStatus.ACTIVE, ""
            account.save(update_fields=["status", "last_error", "updated_at"])
            audit.record("hosting.provisioned", target=account, metadata={"provider_ref": f"native-job-{job.pk}", "correlation_id": job.correlation_id})
            notifications.dispatch("hosting.welcome", email=account.client.email, context={"account": account, "username": account.username, "password": ""})
        elif not ok and account.status == HostingStatus.PENDING:
            account.status, account.last_error = HostingStatus.FAILED, job.result_message or job.get_status_display()
            account.save(update_fields=["status", "last_error", "updated_at"])
            audit.record("hosting.provisioning_failed", target=account, metadata={"error": account.last_error, "correlation_id": job.correlation_id})
    elif not ok:
        account.last_error = f"{job.command} did not complete on the node: {job.result_message or job.get_status_display()}"
        account.save(update_fields=["last_error", "updated_at"])
        audit.record("hosting.native_job_failed", target=account, metadata={"command": job.command, "error": job.result_message, "correlation_id": job.correlation_id})
