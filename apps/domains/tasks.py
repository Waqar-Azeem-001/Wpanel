from celery import shared_task


@shared_task
def sync_tld_prices_task():
    """Daily: bring the domain price list in step with the company website's price list (see ``price_sync``). A failure is recorded and shown on the TLD pricing screen."""
    from . import price_sync

    return price_sync.run_and_record()


@shared_task
def poll_transfers_task():
    """Hourly: inbound transfers take days at the registry; finish the ones that are done."""
    from . import services

    return services.poll_pending_transfers()
