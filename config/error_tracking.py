"""Optional error tracking (Sentry). Off unless SENTRY_DSN is set, so nothing changes for a deployment without one."""
import logging

logger = logging.getLogger(__name__)


def init_error_tracking(dsn: str, environment: str = "production", traces_sample_rate: float = 0.0) -> bool:
    """Start Sentry when a DSN is given. Returns True if started; never raises (a monitoring problem must not stop the site)."""
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.django import DjangoIntegration
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; error tracking is off.")
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        traces_sample_rate=traces_sample_rate,
        send_default_pii=False,  # no emails, IPs or cookies in reports
    )
    return True
